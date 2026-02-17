"""
S3 Client for managing file uploads and downloads.
Simplified client for direct S3 operations like uploading attachments.
"""
import os
import boto3
from typing import Optional, BinaryIO, Union
from botocore.exceptions import ClientError
from botocore.config import Config
from datetime import datetime
import mimetypes


class S3Client:
    """
    Simple S3 client for file upload and download operations.
    
    Args:
        bucket: S3 bucket name
        endpoint_url: S3 endpoint URL (optional, for S3-compatible services like R2)
        access_key: S3 access key ID
        secret_key: S3 secret access key
        region: AWS region (default: "auto")
        prefix: Optional prefix for all keys (e.g., "attachments/")
    """
    
    def __init__(
        self,
        bucket: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: str = "auto",
        prefix: str = "",
    ):
        self.bucket = bucket or os.getenv("R2_BUCKET_NAME") or os.getenv("S3_BUCKET_NAME")
        self.prefix = prefix.rstrip("/")
        
        self._endpoint_url = endpoint_url or os.getenv("R2_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL")
        self._access_key = access_key or os.getenv("R2_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY_ID")
        self._secret_key = secret_key or os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("S3_ACCESS_SECRET")
        self._region = region or os.getenv("R2_REGION") or os.getenv("S3_REGION", "auto")
        
        if not self.bucket:
            raise ValueError("Bucket name must be provided or set in environment variables")
        if not self._access_key or not self._secret_key:
            raise ValueError("Access key and secret key must be provided or set in environment variables")
        
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of boto3 S3 client"""
        if self._client is None:
            config = Config(
                signature_version='s3v4',
                s3={'addressing_style': 'path'}
            )
            
            self._client = boto3.client(
                's3',
                endpoint_url=self._endpoint_url,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name=self._region,
                config=config
            )
        return self._client
    
    def _build_key(self, path: str) -> str:
        """Build full S3 key with prefix"""
        clean_path = path.lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{clean_path}"
        return clean_path
    
    def upload_file(
        self,
        file_path: str,
        content: Union[bytes, BinaryIO],
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Upload a file to S3.
        
        Args:
            file_path: Path/key for the file in S3 (e.g., "attachments/email_123/file.pdf")
            content: File content as bytes or file-like object
            content_type: MIME type (auto-detected if not provided)
            metadata: Optional metadata dict to attach to the file
            
        Returns:
            dict with 'success', 'key', 'url', and optional 'error' fields
        """
        try:
            key = self._build_key(file_path)
            
            if content_type is None:
                content_type, _ = mimetypes.guess_type(file_path)
                if content_type is None:
                    content_type = "application/octet-stream"
            
            extra_args = {
                'ContentType': content_type,
            }
            
            if metadata:
                extra_args['Metadata'] = {k: str(v) for k, v in metadata.items()}
            
            if isinstance(content, bytes):
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=content,
                    **extra_args
                )
            else:
                self.client.upload_fileobj(
                    content,
                    self.bucket,
                    key,
                    ExtraArgs=extra_args
                )
            
            url = self._get_url(key)
            
            return {
                'success': True,
                'key': key,
                'url': url,
                'bucket': self.bucket,
            }
            
        except ClientError as e:
            return {
                'success': False,
                'error': str(e),
                'key': key if 'key' in locals() else None,
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"Unexpected error: {str(e)}",
            }
    
    def download_file(self, file_path: str) -> dict:
        """
        Download a file from S3.
        
        Args:
            file_path: Path/key of the file in S3
            
        Returns:
            dict with 'success', 'content' (bytes), 'metadata', and optional 'error' fields
        """
        try:
            key = self._build_key(file_path)
            
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            content = response['Body'].read()
            
            return {
                'success': True,
                'content': content,
                'metadata': response.get('Metadata', {}),
                'content_type': response.get('ContentType'),
                'size': response.get('ContentLength'),
                'last_modified': response.get('LastModified'),
            }
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return {
                    'success': False,
                    'error': f"File not found: {file_path}",
                }
            return {
                'success': False,
                'error': str(e),
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"Unexpected error: {str(e)}",
            }
    
    def delete_file(self, file_path: str) -> dict:
        """
        Delete a file from S3.
        
        Args:
            file_path: Path/key of the file in S3
            
        Returns:
            dict with 'success' and optional 'error' fields
        """
        try:
            key = self._build_key(file_path)
            self.client.delete_object(Bucket=self.bucket, Key=key)
            
            return {
                'success': True,
                'key': key,
            }
            
        except ClientError as e:
            return {
                'success': False,
                'error': str(e),
            }
    
    def file_exists(self, file_path: str) -> bool:
        """
        Check if a file exists in S3.
        
        Args:
            file_path: Path/key of the file in S3
            
        Returns:
            True if file exists, False otherwise
        """
        try:
            key = self._build_key(file_path)
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False
    
    def list_files(self, prefix: str = "", max_keys: int = 1000) -> dict:
        """
        List files in S3 with optional prefix filter.
        
        Args:
            prefix: Prefix to filter files (e.g., "attachments/")
            max_keys: Maximum number of keys to return
            
        Returns:
            dict with 'success', 'files' (list of file info), and optional 'error' fields
        """
        try:
            full_prefix = self._build_key(prefix) if prefix else self.prefix
            
            response = self.client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=full_prefix,
                MaxKeys=max_keys
            )
            
            files = []
            for obj in response.get('Contents', []):
                files.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'],
                    'etag': obj.get('ETag', '').strip('"'),
                })
            
            return {
                'success': True,
                'files': files,
                'count': len(files),
                'is_truncated': response.get('IsTruncated', False),
            }
            
        except ClientError as e:
            return {
                'success': False,
                'error': str(e),
                'files': [],
            }
    
    def get_presigned_url(
        self,
        file_path: str,
        expiration: int = 3600,
        method: str = 'get_object'
    ) -> dict:
        """
        Generate a presigned URL for temporary access to a file.
        
        Args:
            file_path: Path/key of the file in S3
            expiration: URL expiration time in seconds (default: 1 hour)
            method: S3 method ('get_object' for download, 'put_object' for upload)
            
        Returns:
            dict with 'success', 'url', and optional 'error' fields
        """
        try:
            key = self._build_key(file_path)
            
            url = self.client.generate_presigned_url(
                method,
                Params={'Bucket': self.bucket, 'Key': key},
                ExpiresIn=expiration
            )
            
            return {
                'success': True,
                'url': url,
                'expires_in': expiration,
            }
            
        except ClientError as e:
            return {
                'success': False,
                'error': str(e),
            }
    
    def _get_url(self, key: str) -> str:
        """Generate URL for a file (not presigned, may not be publicly accessible)"""
        if self._endpoint_url:
            return f"{self._endpoint_url.rstrip('/')}/{self.bucket}/{key}"
        return f"https://{self.bucket}.s3.{self._region}.amazonaws.com/{key}"
