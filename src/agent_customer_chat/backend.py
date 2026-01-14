"""
Simple S3-compatible backend for customer chat agent filesystem operations.
Maps root path (/) to threads/{ticket_id}/ in S3.
"""
import os
import re
from typing import Optional, Union
from fnmatch import fnmatch
import asyncio
import boto3
from botocore.exceptions import ClientError
from botocore.config import Config
from deepagents.backends.protocol import BackendProtocol, WriteResult, EditResult
from deepagents.backends.utils import FileInfo, GrepMatch
from langchain.tools import ToolRuntime
from src.models import AppContext


class S3Backend(BackendProtocol):
    """
    Simple S3-compatible backend for customer chat agent filesystem operations.
    Maps root path (/) to threads/{ticket_id}/ in S3.
    
    Args:
        runtime: ToolRuntime with AppContext containing thread/ticket information
    """
    
    def __init__(self, runtime: ToolRuntime[AppContext]):
        self._runtime = runtime
        
        # Handle case where context might not be fully initialized yet
        if runtime.context.thread is None:
            raise RuntimeError("Cannot initialize S3Backend: runtime.context.thread is None")
        
        # Extract ticket_id from thread (thread_id is used as ticket_id for customer chat)
        self.ticket_id = runtime.context.thread.id
        self.s3_prefix = f"threads/{self.ticket_id}"
        
        # Get S3 configuration from environment variables (always the same)
        self.bucket = os.getenv('S3_BUCKET_NAME', 'solven-testing')
        self._s3_endpoint = os.getenv('S3_ENDPOINT_URL')
        self._s3_access = os.getenv('S3_ACCESS_KEY_ID')
        self._s3_secret = os.getenv('S3_SECRET_KEY') or os.getenv('S3_ACCESS_SECRET')
        self._region = os.getenv('S3_REGION', 'us-east-1')
        
        # Lazy initialization
        self._s3_client = None
        self._bucket_checked = False
    
    @property
    def s3_client(self):
        """Lazy S3 client initialization"""
        if self._s3_client is None:
            # Check if endpoint is Supabase (requires path-style URLs)
            config = None
            if self._s3_endpoint and 'supabase.co' in self._s3_endpoint:
                config = Config(s3={'addressing_style': 'path'})
            
            self._s3_client = boto3.client(
                's3',
                endpoint_url=self._s3_endpoint,
                aws_access_key_id=self._s3_access,
                aws_secret_access_key=self._s3_secret,
                region_name=self._region,
                config=config
            )
        return self._s3_client
    
    async def _ensure_bucket_exists(self):
        """Create bucket if it doesn't exist (async)"""
        if self._bucket_checked:
            return
        
        try:
            await asyncio.to_thread(self.s3_client.head_bucket, Bucket=self.bucket)
            self._bucket_checked = True
        except ClientError:
            try:
                await asyncio.to_thread(self.s3_client.create_bucket, Bucket=self.bucket)
                self._bucket_checked = True
            except ClientError as e:
                print(f"Warning: Could not create bucket {self.bucket}: {e}")
                self._bucket_checked = True
    
    def _key(self, path: str) -> str:
        """
        Map virtual path (e.g., /file.md or /subdir/file.md) to S3 key.
        Maps root (/) to threads/{ticket_id}/
        """
        # Remove leading slash and normalize
        clean_path = path.lstrip("/")
        
        # Map to S3 prefix
        if clean_path:
            return f"{self.s3_prefix}/{clean_path}"
        return self.s3_prefix
    
    def _path_from_key(self, key: str) -> str:
        """Convert S3 key back to virtual path"""
        if key.startswith(self.s3_prefix + "/"):
            relative = key[len(self.s3_prefix) + 1:]
            return f"/{relative}"
        elif key == self.s3_prefix:
            return "/"
        # Fallback
        return "/" + key
    
    async def als_info(self, path: str) -> list[FileInfo]:
        """
        List files and directories under the given path (async).
        Returns FileInfo entries sorted by path.
        """
        await self._ensure_bucket_exists()
        
        try:
            result = []
            prefix = self._key(path).rstrip("/")
            if prefix and not prefix.endswith("/"):
                prefix += "/"
            
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, Delimiter='/'):
                # Add directories (CommonPrefixes)
                for common_prefix in page.get('CommonPrefixes', []):
                    dir_path = self._path_from_key(common_prefix['Prefix'].rstrip('/'))
                    result.append({
                        'path': dir_path,
                        'is_dir': True,
                        'size': 0,
                        'modified_at': None
                    })
                
                # Add files
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    
                    # Skip the directory marker itself
                    if key == prefix:
                        continue
                    
                    # Skip directories
                    if key.endswith('/'):
                        continue
                    
                    file_path = self._path_from_key(key)
                    result.append({
                        'path': file_path,
                        'is_dir': False,
                        'size': obj['Size'],
                        'modified_at': obj['LastModified'].isoformat() if obj.get('LastModified') else None
                    })
            
            result.sort(key=lambda x: x['path'])
            return result
            
        except ClientError:
            return []
    
    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """
        Read file content with line numbers (async).
        Returns numbered content or error string.
        """
        await self._ensure_bucket_exists()
        
        try:
            key = self._key(file_path)
            response = await asyncio.to_thread(
                self.s3_client.get_object,
                Bucket=self.bucket,
                Key=key
            )
            
            # Try to decode as UTF-8
            try:
                content = response['Body'].read().decode('utf-8')
            except UnicodeDecodeError:
                return f"Error: File '{file_path}' is a binary file and cannot be read as text."
            
            # Split into lines and apply offset/limit
            lines = content.split('\n')
            
            # Apply offset and limit
            start = offset
            end = min(offset + limit, len(lines))
            selected_lines = lines[start:end]
            
            # Add line numbers (1-indexed)
            numbered_lines = []
            for i, line in enumerate(selected_lines, start=start + 1):
                numbered_lines.append(f"{i}\t{line}")
            
            return '\n'.join(numbered_lines)
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return f"Error: File '{file_path}' not found"
            return f"Error reading file '{file_path}': {str(e)}"
    
    async def agrep_raw(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None
    ) -> Union[list[GrepMatch], str]:
        """
        Search for pattern in files (async).
        Returns list of matches or error string for invalid regex.
        """
        try:
            # Compile regex pattern
            regex = re.compile(pattern)
        except re.error as e:
            return f"Invalid regex pattern: {str(e)}"
        
        matches = []
        
        try:
            # Determine search scope
            search_prefix = self._key(path if path else "/")
            if search_prefix and not search_prefix.endswith("/"):
                search_prefix += "/"
            
            # List all objects
            paginator = self.s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket, Prefix=search_prefix):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    
                    # Skip directories
                    if key.endswith('/'):
                        continue
                    
                    file_path = self._path_from_key(key)
                    
                    # Apply glob filter if specified
                    if glob and not fnmatch(file_path, glob):
                        continue
                    
                    # Read file and search
                    try:
                        response = await asyncio.to_thread(
                            self.s3_client.get_object,
                            Bucket=self.bucket,
                            Key=key
                        )
                        content = response['Body'].read().decode('utf-8')
                        
                        for line_num, line in enumerate(content.split('\n'), 1):
                            if regex.search(line):
                                matches.append(GrepMatch(
                                    path=file_path,
                                    line=line_num,
                                    text=line
                                ))
                    except Exception:
                        # Skip files that can't be read
                        continue
            
            return matches
            
        except ClientError as e:
            return f"Error searching files: {str(e)}"
    
    async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """
        Find files matching glob pattern (async).
        Returns list of FileInfo entries.
        """
        try:
            search_prefix = self._key(path).rstrip("/")
            if search_prefix:
                search_prefix += "/"
            
            result = []
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            for page in paginator.paginate(Bucket=self.bucket, Prefix=search_prefix):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    
                    # Skip directories
                    if key.endswith('/'):
                        continue
                    
                    file_path = self._path_from_key(key)
                    
                    # Apply glob pattern
                    if fnmatch(file_path, pattern) or fnmatch(os.path.basename(file_path), pattern):
                        result.append({
                            'path': file_path,
                            'is_dir': False,
                            'size': obj['Size'],
                            'modified_at': obj['LastModified'].isoformat() if obj.get('LastModified') else None
                        })
            
            # Sort by path
            result.sort(key=lambda x: x['path'])
            return result
            
        except ClientError:
            return []
    
    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """
        Create a new file (create-only semantics, async).
        Returns WriteResult with error if file already exists.
        """
        await self._ensure_bucket_exists()
        
        try:
            key = self._key(file_path)
            
            # Check if file exists
            try:
                await asyncio.to_thread(
                    self.s3_client.head_object,
                    Bucket=self.bucket,
                    Key=key
                )
                return WriteResult(
                    error=f"File '{file_path}' already exists. Use edit to modify existing files.",
                    path=None,
                    files_update=None
                )
            except ClientError as e:
                if e.response['Error']['Code'] != '404':
                    return WriteResult(
                        error=f"Error checking file existence: {str(e)}",
                        path=None,
                        files_update=None
                    )
            
            # Write new file
            await asyncio.to_thread(
                self.s3_client.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=content.encode('utf-8'),
                ContentType='text/plain',
                Metadata={'uploaded-by': 'agent'}
            )
            
            return WriteResult(
                error=None,
                path=file_path,
                files_update=None  # External backend, no state update
            )
            
        except ClientError as e:
            return WriteResult(
                error=f"Error writing file '{file_path}': {str(e)}",
                path=None,
                files_update=None
            )
    
    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False
    ) -> EditResult:
        """
        Edit an existing file by replacing old_string with new_string (async).
        Enforces uniqueness unless replace_all=True.
        """
        await self._ensure_bucket_exists()
        
        try:
            key = self._key(file_path)
            
            # Read current content
            try:
                response = await asyncio.to_thread(
                    self.s3_client.get_object,
                    Bucket=self.bucket,
                    Key=key
                )
                content = response['Body'].read().decode('utf-8')
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    return EditResult(
                        error=f"File '{file_path}' not found",
                        path=None,
                        files_update=None,
                        occurrences=0
                    )
                return EditResult(
                    error=f"Error reading file '{file_path}': {str(e)}",
                    path=None,
                    files_update=None,
                    occurrences=0
                )
            
            # Count occurrences
            occurrences = content.count(old_string)
            
            if occurrences == 0:
                return EditResult(
                    error=f"String '{old_string}' not found in file '{file_path}'",
                    path=None,
                    files_update=None,
                    occurrences=0
                )
            
            # Check uniqueness if not replace_all
            if not replace_all and occurrences > 1:
                return EditResult(
                    error=f"String '{old_string}' appears {occurrences} times. Use replace_all=True to replace all occurrences.",
                    path=None,
                    files_update=None,
                    occurrences=occurrences
                )
            
            # Perform replacement
            if replace_all:
                new_content = content.replace(old_string, new_string)
            else:
                new_content = content.replace(old_string, new_string, 1)
            
            # Write back to S3
            await asyncio.to_thread(
                self.s3_client.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=new_content.encode('utf-8'),
                ContentType='text/plain'
            )
            
            return EditResult(
                error=None,
                path=file_path,
                files_update=None,  # External backend
                occurrences=occurrences
            )
            
        except ClientError as e:
            return EditResult(
                error=f"Error editing file '{file_path}': {str(e)}",
                path=None,
                files_update=None,
                occurrences=0
            )
    
    # Synchronous wrappers for protocol compatibility
    def ls_info(self, path: str) -> list[FileInfo]:
        """Synchronous wrapper for als_info"""
        return asyncio.run(self.als_info(path))
    
    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Synchronous wrapper for aread"""
        return asyncio.run(self.aread(file_path, offset, limit))
    
    def grep_raw(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None
    ) -> Union[list[GrepMatch], str]:
        """Synchronous wrapper for agrep_raw"""
        return asyncio.run(self.agrep_raw(pattern, path, glob))
    
    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """Synchronous wrapper for aglob_info"""
        return asyncio.run(self.aglob_info(pattern, path))
    
    def write(self, file_path: str, content: str) -> WriteResult:
        """Synchronous wrapper for awrite"""
        return asyncio.run(self.awrite(file_path, content))
    
    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False
    ) -> EditResult:
        """Synchronous wrapper for aedit"""
        return asyncio.run(self.aedit(file_path, old_string, new_string, replace_all))
