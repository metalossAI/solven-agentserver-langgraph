"""
S3-compatible backend for DeepAgents using MinIO or AWS S3.
Implements the BackendProtocol for virtual filesystem operations.
"""
import os
import re
import yaml
from datetime import datetime
from typing import Optional, Union
from fnmatch import fnmatch
import asyncio
import boto3
from botocore.exceptions import ClientError
from deepagents.backends.protocol import BackendProtocol, WriteResult, EditResult
from deepagents.backends.utils import FileInfo, GrepMatch


def _parse_skillmd_frontmatter(skillmd: str) -> str:
    """
    Parse and extract the frontmatter from a skillmd file.
    
    Extracts YAML frontmatter from the beginning of a file in the format:
    ---
    name: compraventa-escrituras
    description: Redacta escrituras de compraventa...
    ---
    
    Args:
        skillmd: The content of the skillmd file as a string
        
    Returns:
        The frontmatter string (content between --- delimiters), or empty string if not found
    """
    # Match YAML frontmatter between --- delimiters at the start of the file
    frontmatter_pattern = r'^---\s*\n(.*?)\n---\s*\n'
    match = re.match(frontmatter_pattern, skillmd, re.DOTALL)
    
    if not match:
        return ""
    
    # Return the frontmatter content (group 1 is the content between the --- delimiters)
    return match.group(1)


class S3Backend(BackendProtocol):
    """
    S3-compatible backend for agent filesystem operations.
    Works with MinIO, AWS S3, or any S3-compatible storage.
    
    Virtual Mounts:
        - /workspace -> threads/{thread_id}/ (shared thread workspace)
        - /ticket -> threads/{ticket_id}/ (ticket context files, if ticket_id provided)
        - /skills -> {user_id}/skills/ (user's skills library with categories and resources)
    
    Args:
        bucket: S3 bucket name
        endpoint_url: MinIO/S3 endpoint URL (e.g., "http://localhost:9000")
        access_key: S3 access key
        secret_key: S3 secret key
        region: AWS region (default: "us-east-1")
        scope: Permission scope - 'read' (read-only) or 'write' (read+write). Default: 'write'
        user_id: User ID for skills access
        thread_id: Thread ID for workspace scoping
        ticket_id: Ticket ID for ticket files access
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
        thread_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.user_id = user_id
        self.thread_id = thread_id
        
        # Validate and set scope
        if scope not in ['read', 'write']:
            raise ValueError(f"Invalid scope '{scope}'. Must be 'read' or 'write'.")
        self.scope = scope
        
        # Track loaded skills - used for registering skills with backend (making resources accessible)
        # Note: /skills mount shows ALL skills, not just loaded ones
        self.loaded_skills: set[str] = set()  # e.g., {"compraventa-de-viviendas", "arrendamiento"}
        
        # Mount points: virtual path -> S3 prefix
        self.mounts = {}

        if ticket_id:
            self.mounts["/ticket"] = f"threads/{ticket_id}"
        
        # Workspace mount - thread-specific for shared access
        if thread_id:
            self.mounts["/workspace"] = f"threads/{thread_id}"
        elif user_id:
            # Fallback to user workspace if no thread_id
            self.mounts["/workspace"] = f"{self.prefix}/workspace" if self.prefix else f"{user_id}/workspace"
        
        # Skills mount - user-specific skills (read-only)
        if user_id:
            self.mounts["/skills"] = f"{user_id}/skills"
        
        # Store S3 configuration for lazy client creation
        self._s3_endpoint = endpoint_url or os.getenv('S3_ENDPOINT_URL')
        self._s3_access = access_key or os.getenv('S3_ACCESS_KEY_ID')
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
    
    def load_skill(self, skill_name: str) -> None:
        """
        Register a skill as loaded. This makes the skill's resources accessible in the backend.
        Note: /skills mount shows ALL skills regardless of this registration.
        This method is mainly used to track which skills have been loaded for resource access.
        
        Args:
            skill_name: Skill name (e.g., 'compraventa-de-viviendas')
        """
        self.loaded_skills.add(skill_name)
    
    def unload_skill(self, skill_path: str) -> None:
        """Unregister a skill. Note: /skills mount shows ALL skills regardless of registration."""
        self.loaded_skills.discard(skill_path)
    
    def _resolve_path(self, path: str) -> str:
        """
        Resolve a virtual path (e.g., /skills/foo or /workspace/bar) to a full S3 key.
        Respects mount points defined in self.mounts.
        
        Skills are stored directly under {user_id}/skills/{skill_name}/ without categories.
        """
        # Check mounts first (includes /skills mount)
        for mount, s3_prefix in self.mounts.items():
            if path.startswith(mount):
                relative = path[len(mount):].lstrip("/")
                resolved = f"{s3_prefix}/{relative}" if relative else s3_prefix
                return resolved
        
        # Check mounts for other paths
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
        return resolved
    
    def _path_from_key(self, key: str) -> str:
        """
        Convert S3 key back to virtual path by checking mount points.
        This reverses the _resolve_path operation.
        
        Skills are stored as {user_id}/skills/{skill_name}/... and mapped to /skills/{skill_name}/...
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
    
    def _map_to_md_file(self, file_path: str) -> str:
        """
        Map any filename to its .md version for read/write operations.
        Ensures the file always has a .md extension and no other extensions.
        
        Examples:
        - "document.docx" -> "document.md"
        - "document.pdf" -> "document.md"
        - "document.md" -> "document.md"
        - "file.docx.md" -> "file.md" (removes all extensions before adding .md)
        - "file" -> "file.md"
        
        This allows agents to reference original filenames but work with .md versions.
        """
        # Split path and filename
        dir_path, filename = os.path.split(file_path)
        
        # Remove ALL extensions (handle cases like "file.docx.md" -> "file")
        name_without_ext = filename
        while '.' in name_without_ext and not name_without_ext.endswith('.md'):
            name_without_ext = os.path.splitext(name_without_ext)[0]
        
        # If it already ends with .md, use it as-is (but ensure no double extension)
        if name_without_ext.endswith('.md'):
            base_name = name_without_ext
        else:
            # Add .md extension
            base_name = f"{name_without_ext}.md"
        
        # Reconstruct path
        if dir_path and dir_path != '/':
            return os.path.join(dir_path, base_name)
        elif dir_path == '/':
            return f"/{base_name}"
        return base_name
    
    def _get_original_filename(self, md_file_path: str, available_originals: dict = None) -> str:
        """
        Get the original filename from a .md file path.
        If available_originals dict is provided (from listing context), uses it for efficiency.
        Otherwise checks S3 (slower).
        
        Args:
            md_file_path: Path to .md file
            available_originals: Dict mapping base_name -> original_path (optional, for efficiency)
        
        Returns:
            Original filename path if exists, otherwise returns md_file_path
        """
        if not md_file_path.endswith('.md'):
            return md_file_path
        
        dir_path, md_filename = os.path.split(md_file_path)
        base_name = os.path.splitext(md_filename)[0]
        
        # If we have available originals from listing context, use them
        if available_originals and base_name in available_originals:
            return available_originals[base_name]
        
        # Otherwise check S3 (slower, but works when called outside listing context)
        preferred_extensions = ['.docx', '.pdf', '.txt', '.xlsx', '.pptx']
        
        for ext in preferred_extensions:
            original_filename = f"{base_name}{ext}"
            if dir_path and dir_path != '/':
                original_path = os.path.join(dir_path, original_filename)
            elif dir_path == '/':
                original_path = f"/{original_filename}"
            else:
                original_path = original_filename
            
            try:
                key = self._key(original_path)
                self.s3_client.head_object(Bucket=self.bucket, Key=key)
                return original_path
            except ClientError:
                continue
        
        return md_file_path
    
    def _auto_add_md_extension(self, file_path: str) -> str:
        """
        Automatically add .md extension if not present.
        Strips any existing extension to prevent file.txt.md
        DEPRECATED: Use _map_to_md_file instead for better semantics.
        """
        return self._map_to_md_file(file_path)
    
    def ls_info(self, path: str) -> list[FileInfo]:
        """
        List files and directories under the given path.
        Returns FileInfo entries sorted by path.
        Includes virtual mount points when listing root.
        For /skills, only shows loaded skills.
        """
        self._ensure_bucket_exists()
        try:
            result = []
            
            # If listing root, add mount points as virtual directories
            if path == "/":
                # Add standard mounts (this already includes /skills if user_id is set)
                for mount in self.mounts.keys():
                    result.append({
                        'path': mount,
                        'is_dir': True,
                        'size': 0,
                        'modified_at': None
                    })
                
                return sorted(result, key=lambda x: x['path'])
            
            # Special handling for /skills directory - show ALL skills from S3
            if path == "/skills":
                if not self.user_id:
                    return []
                
                # List all skills directly from S3 (not filtered by loaded_skills)
                skills_prefix = f"{self.user_id}/skills/"
                try:
                    response = self.s3_client.list_objects_v2(
                        Bucket=self.bucket,
                        Prefix=skills_prefix,
                        Delimiter='/'
                    )
                    
                    if 'CommonPrefixes' in response:
                        for skill_prefix_info in response['CommonPrefixes']:
                            skill_prefix = skill_prefix_info['Prefix']
                            # Extract skill name from prefix (e.g., "user_id/skills/compraventa-de-viviendas/" -> "compraventa-de-viviendas")
                            skill_name = skill_prefix.rstrip('/').split('/')[-1]
                            result.append({
                                'path': f'/skills/{skill_name}',
                                'is_dir': True,
                                'size': 0,
                                'modified_at': None
                            })
                except ClientError:
                    pass
                
                return sorted(result, key=lambda x: x['path'])
            
            # For all other paths, list normally
            prefix = self._key(path).rstrip("/")
            if prefix and not prefix.endswith("/"):
                prefix += "/"
            
            # Determine if we're in a skills path (show all files) or workspace/ticket (only .md)
            is_skills_path = path.startswith('/skills/')
            
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
                
                # Track files: md_file_path -> (md_key, md_obj) and original_file_path -> (original_key, original_obj)
                md_files = {}  # base_name -> (md_file_path, md_key, md_obj)
                original_files = {}  # base_name -> (original_file_path, original_key, original_obj)
                
                # First pass: collect all files and categorize
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    
                    # Skip the directory marker itself
                    if key == prefix:
                        continue
                    
                    # Skip directories
                    if key.endswith('/'):
                        continue
                    
                    file_path = self._path_from_key(key)
                    
                    # Skip internal files
                    if file_path.endswith('.editor.json') or file_path.endswith('instructions.md'):
                        continue
                    if file_path.endswith('/instructions.md'):
                        continue
                    
                    # Extract base name (without extension)
                    dir_path, filename = os.path.split(file_path)
                    base_name = os.path.splitext(filename)[0]
                    
                    if key.endswith('.md'):
                        # Store .md file
                        md_files[base_name] = (file_path, key, obj)
                    else:
                        # Store original file (e.g., .docx, .pdf)
                        original_files[base_name] = (file_path, key, obj)
                
                # Second pass: show original filenames when they exist, otherwise show .md files
                shown_bases = set()
                
                # First, show original files that have .md counterparts
                for base_name, (original_path, original_key, original_obj) in original_files.items():
                    if base_name in md_files:
                        # Original exists and .md exists - show original filename
                        result.append({
                            'path': original_path,
                            'is_dir': False,
                            'size': md_files[base_name][2]['Size'],  # Use .md file size
                            'modified_at': md_files[base_name][2]['LastModified'].isoformat() if md_files[base_name][2].get('LastModified') else None
                        })
                        shown_bases.add(base_name)
                
                # Then, show .md files that don't have originals
                for base_name, (md_path, md_key, md_obj) in md_files.items():
                    if base_name not in shown_bases:
                        # No original exists, show .md file
                        result.append({
                            'path': md_path,
                            'is_dir': False,
                            'size': md_obj['Size'],
                            'modified_at': md_obj['LastModified'].isoformat() if md_obj.get('LastModified') else None
                        })
                
                # Finally, show original files that don't have .md counterparts (shouldn't happen in practice)
                for base_name, (original_path, original_key, original_obj) in original_files.items():
                    if base_name not in shown_bases:
                        result.append({
                            'path': original_path,
                            'is_dir': False,
                            'size': original_obj['Size'],
                            'modified_at': original_obj['LastModified'].isoformat() if original_obj.get('LastModified') else None
                        })
            result.sort(key=lambda x: x['path'])
            return result
            
        except ClientError:
            return []
    
    def read(self, file_path: str, offset: int = 0, limit: int = 2000, allow_non_markdown: bool = False) -> str:
        """
        Read file content with line numbers.
        Returns numbered content or error string.
        
        Args:
            file_path: Path to the file (virtual path with mount support)
            offset: Line offset to start reading from
            limit: Maximum number of lines to read
            allow_non_markdown: If True, allows reading non-markdown files (e.g., from /skills)
        """
        self._ensure_bucket_exists()
        
        # Map original filename to .md version for read operations
        # This allows agents to reference "document.docx" but read "document.md"
        file_path = self._map_to_md_file(file_path)
        
        # Block access to instructions.md files (internal configuration)
        if file_path.endswith('/instructions.md') or file_path.endswith('instructions.md'):
            return f"Error: File '{file_path}' not found"
        
        # Validate markdown file (skip validation if explicitly allowed)
        if not allow_non_markdown:
            error = self._ensure_markdown_file(file_path)
            if error:
                return error
        
        try:
            key = self._key(file_path)
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            
            # Try to decode as UTF-8, handle binary files
            try:
                content = response['Body'].read().decode('utf-8')
            except UnicodeDecodeError:
                return f"Error: File '{file_path}' is a binary file and cannot be read as text. Binary files (PDFs, images) are not directly readable by the agent."
            
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
                    
                    # Skip directories
                    if key.endswith('/'):
                        continue
                    
                    # Only search in .md files (agents can only work with markdown)
                    # Check the actual S3 key extension, not the virtual path
                    if not key.endswith('.md'):
                        continue
                    
                    file_path = self._path_from_key(key)
                    
                    # Apply glob filter if specified
                    if glob and not fnmatch(file_path, glob):
                        continue
                    
                    # Read file and search
                    try:
                        response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
                        content = response['Body'].read().decode('utf-8')
                        
                        for line_num, line in enumerate(content.split('\n'), 1):
                            if regex.search(line):
                                # Use original filename for display if it exists
                                display_path = self._get_original_filename(file_path)
                                matches.append(GrepMatch(
                                    path=display_path,
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
                    
                    # Skip directories
                    if key.endswith('/'):
                        continue
                    
                    # Only return .md files (agents can only work with markdown)
                    # Check the actual S3 key extension, not the virtual path
                    if not key.endswith('.md'):
                        continue
                    
                    file_path = self._path_from_key(key)
                    
                    # Map to original filename for display (if original exists)
                    display_path = self._get_original_filename(file_path)
                    
                    # Apply glob pattern (check both original and .md paths)
                    if fnmatch(file_path, pattern) or fnmatch(display_path, pattern) or \
                       fnmatch(os.path.basename(file_path), pattern) or fnmatch(os.path.basename(display_path), pattern):
                        result.append({
                            'path': display_path,
                            'is_dir': False,
                            'size': obj['Size'],
                            'modified_at': obj['LastModified'].isoformat() if obj.get('LastModified') else None
                        })
            
            # Sort by path
            result.sort(key=lambda x: x['path'])
            return result
            
        except ClientError:
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
        
        # Map original filename to .md version for write operations
        # This ensures the file is always markdown and removes any non-.md extensions
        # Example: "document.docx" -> "document.md", "file.pdf.md" -> "file.md"
        original_path = file_path
        file_path = self._map_to_md_file(file_path)
        
        # Validate markdown file (double-check that it ends with .md)
        error = self._ensure_markdown_file(file_path)
        if error:
            return WriteResult(
                error=error,
                path=None,
                files_update=None
            )
        
        # Additional validation: ensure no non-.md extensions remain
        if not file_path.endswith('.md'):
            return WriteResult(
                error=f"Error: File path must end with .md extension. Got: '{file_path}'",
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

    def write_bytes(self, file_path: str, content: bytes, content_type: str = "application/octet-stream") -> WriteResult:
        """
        Create a new binary file (create-only semantics).
        Returns WriteResult with error if file already exists.
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
        
        # For write_bytes, also enforce .md extension (agents should only write markdown)
        # Map original filename to .md version
        original_path = file_path
        file_path = self._map_to_md_file(file_path)
        
        # Validate markdown file
        error = self._ensure_markdown_file(file_path)
        if error:
            return WriteResult(
                error=error,
                path=None,
                files_update=None
            )
        
        # Additional validation: ensure no non-.md extensions remain
        if not file_path.endswith('.md'):
            return WriteResult(
                error=f"Error: File path must end with .md extension. Got: '{file_path}'",
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

            # Write file (always markdown, so use text/markdown content type)
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType='text/markdown',  # Always markdown for agent-written files
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
        
        # Map original filename to .md version for edit operations
        # This allows agents to reference "document.docx" but edit "document.md"
        file_path = self._map_to_md_file(file_path)
        
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
            
        except ClientError:
            return []
    
    def _parse_skill_frontmatter(self, content: str) -> dict:
        """
        Parse YAML frontmatter from skill content.
        
        Args:
            content: Full content of SKILL.md file
            
        Returns:
            Dictionary with 'name' and 'description' from frontmatter, or empty dict if not found
        """
        try:
            # Check if content starts with ---
            if not content.startswith('---'):
                return {}
            
            # Find the closing ---
            end_idx = content.find('---', 3)
            if end_idx == -1:
                return {}
            
            # Extract and parse YAML
            frontmatter = content[3:end_idx].strip()
            metadata = yaml.safe_load(frontmatter)
            
            return {
                'name': metadata.get('name', ''),
                'description': metadata.get('description', '')
            }
        except Exception as e:
            print(f"Error parsing frontmatter: {e}")
            return {}
    
    async def load_skills_frontmatter(self, category: Optional[str] = None) -> str:
        """
        Load all skills frontmatter as concatenated YAML blocks.
        Returns the raw frontmatter from each SKILL.md file concatenated together.
        
        Args:
            category: Deprecated - kept for backward compatibility, ignored.
        
        Returns:
            Concatenated YAML frontmatter blocks from all skills, each wrapped in ---
        """
        if not self.user_id:
            return ""
            
        skills_prefix = f"{self.user_id}/skills/"
        frontmatter_blocks = []
        
        try:
            # List all skills directly under skills/ (no categories)
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=skills_prefix,
                Delimiter='/'
            )
            
            if 'CommonPrefixes' not in response:
                return ""
            
            for skill_prefix_info in response['CommonPrefixes']:
                skill_prefix = skill_prefix_info['Prefix']
                
                # Try to load SKILL.md and extract frontmatter
                skill_file_key = f"{skill_prefix}SKILL.md"
                try:
                    response = self.s3_client.get_object(Bucket=self.bucket, Key=skill_file_key)
                    content = response['Body'].read().decode('utf-8')
                    
                    # Extract frontmatter using local function
                    frontmatter = _parse_skillmd_frontmatter(content)
                    
                    if frontmatter:
                        frontmatter_blocks.append(f"---\n{frontmatter}\n---")
                except Exception:
                    # Skip skills that can't be read
                    continue
            
            return "\n".join(frontmatter_blocks)
            
        except ClientError:
            return ""
    
    async def load_all_skills_formatted(self, category: Optional[str] = None) -> str:
        """
        Load all skills organized by categories and subcategories in a formatted string.
        Reads SKILL.md frontmatter to extract name and description for each skill.
        
        Args:
            category: Optional category to filter skills (e.g., 'escrituras'). 
                      If provided, only shows skills from that category.
        
        Returns:
            Formatted string with skills organized by category, including descriptions
        """
        if not self.user_id:
            return "No user ID available"
            
        skills_prefix = f"{self.user_id}/skills/"
        
        try:
            # If category is specified, only list that category
            if category:
                category_prefix = f"{skills_prefix}{category}/"
                
                # List skills in this specific category
                category_response = self.s3_client.list_objects_v2(
                    Bucket=self.bucket,
                    Prefix=category_prefix,
                    Delimiter='/'
                )
                
                if 'CommonPrefixes' not in category_response:
                    return f"No skills found in category '{category}'"
                
                result = f"Habilidades disponibles en **{category.upper()}**:\n\n"
                
                for skill_prefix in category_response['CommonPrefixes']:
                    skill_path = skill_prefix['Prefix'].replace(skills_prefix, '').rstrip('/')
                    skill_name = skill_path.split('/')[-1]
                    
                    # Try to load SKILL.md and parse frontmatter
                    skill_file_key = f"{skill_prefix['Prefix']}SKILL.md"
                    try:
                        response = self.s3_client.get_object(Bucket=self.bucket, Key=skill_file_key)
                        content = response['Body'].read().decode('utf-8')
                        metadata = self._parse_skill_frontmatter(content)
                        
                        if metadata.get('description'):
                            result += f"   └─ **{skill_name}** (`{skill_path}`)\n"
                            result += f"      {metadata['description']}\n\n"
                        else:
                            result += f"   └─ {skill_name} (`{skill_path}`)\n"
                    except Exception as e:
                        # If we can't read the file, just show the skill name
                        result += f"   └─ {skill_name} (`{skill_path}`)\n"
                
                result += "\n💡 Para cargar una habilidad, usa: `load_skill('categoria/nombre_habilidad')`"
                return result
            
            # Otherwise, list all categories
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=skills_prefix,
                Delimiter='/'
            )
            
            if 'CommonPrefixes' not in response:
                return "No skills found"
            
            result = "Habilidades disponibles:\n\n"
            
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
                    result += f"**{category_name.upper()}**\n"
                    
                    for skill_prefix in category_response['CommonPrefixes']:
                        skill_path = skill_prefix['Prefix'].replace(skills_prefix, '').rstrip('/')
                        skill_name = skill_path.split('/')[-1]
                        
                        # Try to load SKILL.md and parse frontmatter
                        skill_file_key = f"{skill_prefix['Prefix']}SKILL.md"
                        try:
                            response = self.s3_client.get_object(Bucket=self.bucket, Key=skill_file_key)
                            content = response['Body'].read().decode('utf-8')
                            metadata = self._parse_skill_frontmatter(content)
                            
                            if metadata.get('description'):
                                result += f"   └─ **{skill_name}** (`{skill_path}`)\n"
                                result += f"      {metadata['description']}\n\n"
                            else:
                                result += f"   └─ {skill_name} (`{skill_path}`)\n"
                        except Exception as e:
                            # If we can't read the file, just show the skill name
                            result += f"   └─ {skill_name} (`{skill_path}`)\n"
                    
                    result += "\n"
            
            result += "\nPara cargar una habilidad, usa: `load_skill('categoria/nombre_habilidad')`"
            return result
            
        except ClientError as e:
            return f"Error al cargar habilidades: {str(e)}"
    
    async def load_skill_content(self, skill_name: str) -> Optional[str]:
        """
        Load the SKILL.md content for a specific skill.
        
        Args:
            skill_name: Skill name (e.g., "compraventa-de-viviendas")
        
        Returns:
            Content of SKILL.md file or None if not found
        """
        if not self.user_id:
            return None
            
        skill_md_path = f"{self.user_id}/skills/{skill_name}/SKILL.md"
        
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=skill_md_path
            )
            content = response['Body'].read().decode('utf-8')
            return content
        except ClientError:
            return None
        except Exception:
            return None
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
    - S3_ACCESS_KEY_ID: Access key
    - S3_SECRET_KEY: Secret key
    - S3_PREFIX: Optional prefix for all paths (default: "")
    - S3_REGION: AWS region (default: "us-east-1")
    """
    return S3Backend(
        bucket=os.getenv('S3_BUCKET', 'agent-files'),
        prefix=os.getenv('S3_PREFIX', ''),
        endpoint_url=os.getenv('S3_ENDPOINT_URL'),
        access_key=os.getenv('S3_ACCESS_KEY_ID'),
        secret_key=os.getenv('S3_SECRET_KEY'),
        region=os.getenv('S3_REGION', 'us-east-1')
    )

def get_user_backend_sync(user_id: str, conversation_id: Optional[str] = None, scope: str = "write") -> S3Backend:
    return S3Backend(
        bucket=os.getenv('S3_BUCKET', 'scriba'),
        prefix=user_id,
        endpoint_url=os.getenv('S3_ENDPOINT_URL'),
        access_key=os.getenv('S3_ACCESS_KEY_ID'),
        secret_key=os.getenv('S3_SECRET_KEY'),
        region=os.getenv('S3_REGION', 'us-east-1'),
        scope=scope,
        user_id=user_id,
        conversation_id=conversation_id
    )

async def get_user_s3_backend(user_id: str, thread_id: Optional[str] = None, ticket_id: Optional[str] = None, scope: str = "write") -> S3Backend:
    """
    Create S3Backend scoped to a specific user and optionally a thread and ticket.
    Provides virtual mount points for organized file access.
    
    Args:
        user_id: User ID for skills access
        thread_id: Optional thread ID for shared workspace scoping
        ticket_id: Optional ticket ID for ticket files access
        scope: Permission scope - 'read' (read-only) or 'write' (read+write). Default: 'write'
    
    Returns:
        S3Backend instance with virtual mounts:
        - /workspace -> threads/{thread_id}/ (shared thread workspace)
        - /ticket -> threads/{ticket_id}/ (ticket context files, if ticket_id provided)
        - /skills -> {user_id}/skills/ (user's skills library)
        
    Example usage by agent:
        backend.read("/workspace/notes.md")  # Read from thread workspace
        backend.read("/ticket/requirements.md")  # Read from ticket files
        backend.read("/skills/escrituras/compraventa/SKILL.md")  # Read skill
        backend.read("/skills/escrituras/compraventa/plantilla.pdf")  # Read skill resource
    """
    
    return S3Backend(
        bucket=os.getenv('S3_BUCKET', 'scriba'),
        prefix="",  # No user prefix for shared threads
        endpoint_url=os.getenv('S3_ENDPOINT_URL'),
        access_key=os.getenv('S3_ACCESS_KEY_ID'),
        secret_key=os.getenv('S3_SECRET_KEY'),
        region=os.getenv('S3_REGION', 'us-east-1'),
        scope=scope,
        user_id=user_id,
        thread_id=thread_id,
        ticket_id=ticket_id
    )
