"""
S3-compatible backend for DeepAgents using MinIO or AWS S3.
Implements the BackendProtocol for virtual filesystem operations.
"""
import os
import re
from datetime import datetime
from typing import Optional, Union
from fnmatch import fnmatch
import asyncio
import boto3
from botocore.exceptions import ClientError
from deepagents.backends.protocol import BackendProtocol, WriteResult, EditResult
from deepagents.backends.utils import FileInfo, GrepMatch


class S3Backend(BackendProtocol):
    """
    S3-compatible backend for agent filesystem operations.
    Works with MinIO, AWS S3, or any S3-compatible storage.
    
    Args:
        bucket: S3 bucket name
        prefix: Optional prefix for all paths (e.g., "agents/scriba/")
        endpoint_url: MinIO/S3 endpoint URL (e.g., "http://localhost:9000")
        access_key: S3 access key
        secret_key: S3 secret key
        region: AWS region (default: "us-east-1")
        scope: Permission scope - 'read' (read-only) or 'write' (read+write). Default: 'write'
    """
    
    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: str = "us-east-1",
        scope: str = "write",
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        skills_prefix: Optional[str] = None
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.user_id = user_id
        self.conversation_id = conversation_id
        
        # Validate and set scope
        if scope not in ['read', 'write']:
            raise ValueError(f"Invalid scope '{scope}'. Must be 'read' or 'write'.")
        self.scope = scope
        
        # Mount points: virtual path -> S3 prefix
        self.mounts = {}
        if user_id:
            # Workspace mount - conversation-specific or user-level
            if conversation_id:
                self.mounts["/workspace"] = f"{self.prefix}/conversations/{conversation_id}" if self.prefix else f"{user_id}/conversations/{conversation_id}"
            else:
                self.mounts["/workspace"] = f"{self.prefix}/workspace" if self.prefix else f"{user_id}/workspace"
            
            # Skills mount - read-only user skills
            if skills_prefix:
                self.mounts["/skills"] = skills_prefix
            else:
                # Default: user_id/skills
                self.mounts["/skills"] = f"{user_id}/skills"
        
        print(f"[S3Backend] Initialized with bucket='{bucket}', prefix='{self.prefix}', scope='{self.scope}'")
        print(f"[S3Backend] Mounts: {self.mounts}")
        
        # Store S3 configuration for lazy client creation
        self._s3_endpoint = endpoint_url or os.getenv('S3_ENDPOINT_URL')
        self._s3_access = access_key or os.getenv('S3_ACCESS_KEY')
        self._s3_secret = secret_key or os.getenv('S3_ACCESS_SECRET') or os.getenv('S3_SECRET_KEY')
        self._region = region
        
        # Lazy initialization
        self._s3_client = None
        self._bucket_checked = False
    
    @property
    def s3_client(self):
        """Lazy S3 client initialization"""
        if self._s3_client is None:
            self._s3_client = boto3.client(
                's3',
                endpoint_url=self._s3_endpoint,
                aws_access_key_id=self._s3_access,
                aws_secret_access_key=self._s3_secret,
                region_name=self._region
            )
        return self._s3_client
    
    def _ensure_bucket_exists(self):
        """Create bucket if it doesn't exist (lazy initialization)"""
        if self._bucket_checked:
            return
        
        try:
            self.s3_client.head_bucket(Bucket=self.bucket)
            self._bucket_checked = True
        except ClientError:
            try:
                self.s3_client.create_bucket(Bucket=self.bucket)
                self._bucket_checked = True
            except ClientError as e:
                print(f"Warning: Could not create bucket {self.bucket}: {e}")
                self._bucket_checked = True  # Don't keep retrying
    
    def _resolve_path(self, path: str) -> str:
        """
        Resolve a virtual path (e.g., /skills/foo or /workspace/bar) to a full S3 key.
        Respects mount points defined in self.mounts.
        """
        for mount, s3_prefix in self.mounts.items():
            if path.startswith(mount):
                relative = path[len(mount):].lstrip("/")
                resolved = f"{s3_prefix}/{relative}" if relative else s3_prefix
                return resolved
        
        # Fallback: treat as workspace path if mounts exist
        if "/workspace" in self.mounts:
            relative = path.lstrip("/")
            workspace_prefix = self.mounts["/workspace"]
            return f"{workspace_prefix}/{relative}" if relative else workspace_prefix
        
        # No mounts: use legacy prefix behavior
        clean_path = path.lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{clean_path}"
        return clean_path
    
    def _key(self, path: str) -> str:
        """Map virtual path to actual S3 key respecting mounts."""
        resolved = self._resolve_path(path)
        print(f"[S3Backend] _key: path='{path}' -> resolved='{resolved}'")
        return resolved
    
    def _path_from_key(self, key: str) -> str:
        """
        Convert S3 key back to virtual path by checking mount points.
        This reverses the _resolve_path operation.
        """
        # Check if key matches any mount point
        for mount, s3_prefix in self.mounts.items():
            if key.startswith(s3_prefix + "/"):
                # Extract relative path and prepend mount point
                relative = key[len(s3_prefix) + 1:]
                return f"{mount}/{relative}"
            elif key == s3_prefix:
                # Exact match to mount point
                return mount
        
        # Fallback: check if it's under workspace mount
        if "/workspace" in self.mounts:
            workspace_prefix = self.mounts["/workspace"]
            if key.startswith(workspace_prefix + "/"):
                relative = key[len(workspace_prefix) + 1:]
                return f"/{relative}"
            elif key == workspace_prefix:
                return "/"
        
        # Legacy behavior: remove prefix
        if self.prefix and key.startswith(self.prefix + "/"):
            return "/" + key[len(self.prefix) + 1:]
        return "/" + key
    
    def _ensure_markdown_file(self, file_path: str) -> Optional[str]:
        """Ensure file path ends with .md extension. Returns error message if invalid, None if valid."""
        if not file_path.endswith('.md'):
            return f"Error: Only markdown (.md) files are allowed. File '{file_path}' is not a markdown file."
        return None
    
    def _check_write_permission(self) -> Optional[str]:
        """Check if write operations are allowed. Returns error message if not allowed, None if allowed."""
        if self.scope == 'read':
            return "Error: Write operations are not allowed in read-only mode."
        return None
    
    def _auto_add_md_extension(self, file_path: str) -> str:
        """
        Automatically add .md extension if not present.
        Strips any existing extension to prevent file.txt.md
        """
        if not file_path.endswith('.md'):
            # Split path and filename
            dir_path, filename = os.path.split(file_path)
            
            # Remove any existing extension
            name_without_ext = os.path.splitext(filename)[0]
            
            # Reconstruct path with .md extension using os.path.join to avoid double slashes
            new_filename = f"{name_without_ext}.md"
            if dir_path and dir_path != '/':
                return os.path.join(dir_path, new_filename)
            elif dir_path == '/':
                return f"/{new_filename}"
            return new_filename
        return file_path
    
    def ls_info(self, path: str) -> list[FileInfo]:
        """
        List files and directories under the given path.
        Returns FileInfo entries sorted by path.
        Includes virtual mount points when listing root.
        """
        self._ensure_bucket_exists()
        try:
            result = []
            
            # If listing root and we have mounts, add mount points as virtual directories
            if path == "/" and self.mounts:
                for mount in self.mounts.keys():
                    result.append({
                        'path': mount,
                        'is_dir': True,
                        'size': 0,
                        'modified_at': None
                    })
            
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
                
                # Add files (only .md files, skip .editor.json)
                for obj in page.get('Contents', []):
                    # Skip the directory marker itself
                    if obj['Key'] == prefix:
                        continue
                    
                    file_path = self._path_from_key(obj['Key'])
                    
                    # Only include .md files, skip .editor.json and other files
                    if not file_path.endswith('.md'):
                        continue
                    
                    result.append({
                        'path': file_path,
                        'is_dir': False,
                        'size': obj['Size'],
                        'modified_at': obj['LastModified'].isoformat() if obj.get('LastModified') else None
                    })
            result.sort(key=lambda x: x['path'])
            return result
            
        except ClientError as e:
            print(f"Error listing path {path}: {e}")
            return []
    
    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """
        Read file content with line numbers.
        Returns numbered content or error string.
        """
        self._ensure_bucket_exists()
        
        # Auto-add .md extension if not present
        file_path = self._auto_add_md_extension(file_path)
        
        # Validate markdown file
        error = self._ensure_markdown_file(file_path)
        if error:
            return error
        
        try:
            key = self._key(file_path)
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            content = response['Body'].read().decode('utf-8')
            
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
    
    def grep_raw(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None
    ) -> Union[list[GrepMatch], str]:
        """
        Search for pattern in files.
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
                    file_path = self._path_from_key(key)
                    
                    # Apply glob filter if specified
                    if glob and not fnmatch(file_path, glob):
                        continue
                    
                    # Skip directories
                    if key.endswith('/'):
                        continue
                    
                    # Read file and search
                    try:
                        response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
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
    
    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """
        Find files matching glob pattern.
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
            
        except ClientError as e:
            print(f"Error in glob_info: {e}")
            return []

    def write(self, file_path: str, content: str) -> WriteResult:
        """
        Create a new file (create-only semantics).
        Returns WriteResult with error if file already exists.
        Only allows .md files.
        Requires 'write' scope.
        /skills is read-only.
        """
        # Check if path is under /skills (read-only)
        if file_path.startswith("/skills"):
            return WriteResult(
                error="Error: /skills is read-only. Cannot create files in skills directory.",
                path=None,
                files_update=None
            )
        
        # Check write permission
        perm_error = self._check_write_permission()
        if perm_error:
            return WriteResult(
                error=perm_error,
                path=None,
                files_update=None
            )
        
        self._ensure_bucket_exists()
        
        # Auto-add .md extension if not present
        file_path = self._auto_add_md_extension(file_path)
        
        # Validate markdown file
        error = self._ensure_markdown_file(file_path)
        if error:
            return WriteResult(
                error=error,
                path=None,
                files_update=None
            )
        
        try:
            key = self._key(file_path)
            try:
                self.s3_client.head_object(Bucket=self.bucket, Key=key)
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
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content.encode('utf-8'),
                ContentType='text/markdown',
                Metadata={
                    'uploaded-by': 'agent',
                }
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
    
    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False
    ) -> EditResult:
        """
        Edit an existing file by replacing old_string with new_string.
        Enforces uniqueness unless replace_all=True.
        Only allows .md files.
        Requires 'write' scope.
        /skills is read-only.
        """
        # Check if path is under /skills (read-only)
        if file_path.startswith("/skills"):
            return EditResult(
                error="Error: /skills is read-only. Cannot edit files in skills directory.",
                path=None,
                files_update=None,
                occurrences=0
            )
        
        # Check write permission
        perm_error = self._check_write_permission()
        if perm_error:
            return EditResult(
                error=perm_error,
                path=None,
                files_update=None,
                occurrences=0
            )
        
        self._ensure_bucket_exists()
        
        # Auto-add .md extension if not present
        file_path = self._auto_add_md_extension(file_path)
        
        # Validate markdown file
        error = self._ensure_markdown_file(file_path)
        if error:
            return EditResult(
                error=error,
                path=None,
                files_update=None,
                occurrences=0
            )
        
        try:
            key = self._key(file_path)
            
            # Read current content
            try:
                response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
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
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=new_content.encode('utf-8'),
                ContentType='text/markdown'
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
    
    async def load_skills(self, category: str = "all") -> list[str]:
        """
        Load available skills for the user from S3.
        
        Args:
            category: Specific category to filter skills, or "all" for all categories
        
        Returns:
            List of skill paths in format "category/skill_name"
        """
        if not self.user_id:
            return []
            
        skills_prefix = f"{self.user_id}/skills/"
        
        try:
            # List all objects under the skills prefix
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=skills_prefix,
                Delimiter='/'
            )
            
            skills = []
            
            if category == "all":
                # Get all categories (common prefixes)
                if 'CommonPrefixes' in response:
                    for prefix_info in response['CommonPrefixes']:
                        category_prefix = prefix_info['Prefix']
                        # List skills in this category
                        category_response = self.s3_client.list_objects_v2(
                            Bucket=self.bucket,
                            Prefix=category_prefix,
                            Delimiter='/'
                        )
                        
                        if 'CommonPrefixes' in category_response:
                            for skill_prefix in category_response['CommonPrefixes']:
                                # Extract category/skill_name from full path
                                skill_path = skill_prefix['Prefix'].replace(skills_prefix, '').rstrip('/')
                                skills.append(skill_path)
            else:
                # Get skills from specific category
                category_prefix = f"{skills_prefix}{category}/"
                category_response = self.s3_client.list_objects_v2(
                    Bucket=self.bucket,
                    Prefix=category_prefix,
                    Delimiter='/'
                )
                
                if 'CommonPrefixes' in category_response:
                    for skill_prefix in category_response['CommonPrefixes']:
                        skill_path = skill_prefix['Prefix'].replace(skills_prefix, '').rstrip('/')
                        skills.append(skill_path)
            
            return skills
            
        except ClientError as e:
            print(f"Error loading skills: {e}")
            return []
    
    async def load_all_skills_formatted(self) -> str:
        """
        Load all skills organized by categories and subcategories in a formatted string.
        
        Returns:
            Formatted string with all skills organized by category
        """
        if not self.user_id:
            return "No user ID available"
            
        skills_prefix = f"{self.user_id}/skills/"
        
        try:
            # List all categories
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=skills_prefix,
                Delimiter='/'
            )
            
            if 'CommonPrefixes' not in response:
                return "No skills found"
            
            result = "📚 Habilidades disponibles:\n\n"
            
            for prefix_info in response['CommonPrefixes']:
                category_prefix = prefix_info['Prefix']
                category_name = category_prefix.replace(skills_prefix, '').rstrip('/')
                
                # List skills in this category
                category_response = self.s3_client.list_objects_v2(
                    Bucket=self.bucket,
                    Prefix=category_prefix,
                    Delimiter='/'
                )
                
                if 'CommonPrefixes' in category_response:
                    result += f"📁 **{category_name.upper()}**\n"
                    
                    for skill_prefix in category_response['CommonPrefixes']:
                        skill_path = skill_prefix['Prefix'].replace(skills_prefix, '').rstrip('/')
                        skill_name = skill_path.split('/')[-1]
                        result += f"   └─ {skill_name} (`{skill_path}`)\n"
                    
                    result += "\n"
            
            result += "\n💡 Para cargar una habilidad, usa: `load_skill('categoria/nombre_habilidad')`"
            return result
            
        except ClientError as e:
            print(f"Error loading skills: {e}")
            return f"Error al cargar habilidades: {str(e)}"
    
    async def load_skill_content(self, skill_path: str) -> Optional[str]:
        """
        Load the SKILL.md content for a specific skill.
        
        Args:
            skill_path: Skill path in format "category/skill_name"
        
        Returns:
            Content of SKILL.md file or None if not found
        """
        if not self.user_id:
            return None
            
        skill_md_path = f"{self.user_id}/skills/{skill_path}/SKILL.md"
        
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=skill_md_path
            )
            content = response['Body'].read().decode('utf-8')
            return content
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                print(f"SKILL.md not found at {skill_md_path}")
            else:
                print(f"Error loading skill content: {e}")
            return None


def create_s3_backend(
    bucket: str = "agent-files",
    prefix: str = "",
    endpoint_url: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None
) -> S3Backend:
    """
    Factory function to create an S3Backend instance.
    
    Usage:
        backend = create_s3_backend(
            bucket="my-agent-files",
            prefix="scriba",
            endpoint_url="http://localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin"
        )
        
        agent = create_deep_agent(backend=backend)
    """
    return S3Backend(
        bucket=bucket,
        prefix=prefix,
        endpoint_url=endpoint_url,
        access_key=access_key,
        secret_key=secret_key
    )

"""
Configuration for S3/MinIO backend.
"""



def get_s3_backend_from_env() -> S3Backend:
    """
    Create S3Backend from environment variables.
    
    Required environment variables:
    - S3_BUCKET: Bucket name (default: "agent-files")
    - S3_ENDPOINT_URL: MinIO/S3 endpoint (e.g., "http://localhost:9000")
    - S3_ACCESS_KEY: Access key
    - S3_SECRET_KEY: Secret key
    - S3_PREFIX: Optional prefix for all paths (default: "")
    - S3_REGION: AWS region (default: "us-east-1")
    """
    return S3Backend(
        bucket=os.getenv('S3_BUCKET', 'agent-files'),
        prefix=os.getenv('S3_PREFIX', ''),
        endpoint_url=os.getenv('S3_ENDPOINT_URL'),
        access_key=os.getenv('S3_ACCESS_KEY'),
        secret_key=os.getenv('S3_SECRET_KEY'),
        region=os.getenv('S3_REGION', 'us-east-1')
    )

def get_user_backend_sync(user_id: str, conversation_id: Optional[str] = None, scope: str = "write") -> S3Backend:
    return S3Backend(
        bucket=os.getenv('S3_BUCKET', 'scriba'),
        prefix=user_id,
        endpoint_url=os.getenv('S3_ENDPOINT_URL'),
        access_key=os.getenv('S3_ACCESS_KEY'),
        secret_key=os.getenv('S3_SECRET_KEY'),
        region=os.getenv('S3_REGION', 'us-east-1'),
        scope=scope,
        user_id=user_id,
        conversation_id=conversation_id
    )

async def get_user_s3_backend(user_id: str, conversation_id: Optional[str] = None, scope: str = "write") -> S3Backend:
    """
    Create S3Backend scoped to a specific user and optionally a conversation.
    Exposes /workspace for conversation files and /skills for user skills (read-only).
    
    Args:
        user_id: User ID to scope the backend to
        conversation_id: Optional conversation ID for further scoping
        scope: Permission scope - 'read' (read-only) or 'write' (read+write). Default: 'write'
    
    Returns:
        S3Backend instance with appropriate prefix, scope, and mount points
        - /workspace -> {user_id}/conversations/{conversation_id}
        - /skills -> {user_id}/skills (read-only)
    """
    
    return S3Backend(
        bucket=os.getenv('S3_BUCKET', 'scriba'),
        prefix=user_id,
        endpoint_url=os.getenv('S3_ENDPOINT_URL'),
        access_key=os.getenv('S3_ACCESS_KEY'),
        secret_key=os.getenv('S3_SECRET_KEY'),
        region=os.getenv('S3_REGION', 'us-east-1'),
        scope=scope,
        user_id=user_id,
        conversation_id=conversation_id
    )
