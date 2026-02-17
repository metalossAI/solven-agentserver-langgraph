# template.py
import os
from dotenv import load_dotenv
from e2b import Template, wait_for_timeout

load_dotenv()

# Build environment variables dictionary
# NOTE: S3 credentials are NOT included here - they must be passed at sandbox creation time
# to ensure mounts happen with actual THREAD_ID/USER_ID values, not during template build
env_vars = {
    "PATH": "/root/.bun/bin:/root/.cargo/bin:/root/.local/bin:/usr/local/bin:$PATH",
    "UV_HOME": "/root/.uv",
    "R2_BUCKET_ENV": os.getenv("R2_BUCKET_ENV", "testing"),
    "THREAD_ID": "",  # Will be set at sandbox creation
    "USER_ID": "",    # Will be set at sandbox creation
    "TICKET_ID": "",  # Optional, set if ticket exists
    # S3 credentials will be set at sandbox creation:
    # - S3_BUCKET_NAME
    # - S3_ACCESS_KEY_ID
    # - S3_ACCESS_SECRET
    # - S3_ENDPOINT_URL
    # - S3_REGION
}

template = (
    Template()
    .from_base_image()
    # ============================================================================
    # System / OS-Level Dependencies (APT)
    # ============================================================================
    # S3 mounting dependencies (rclone)
    .apt_install([
        "fuse3",
        "libfuse2",
        "curl",
    ])
    # Install rclone (required for S3 bucket mounting)
    .run_cmd("curl https://rclone.org/install.sh | bash", user="root")
    # Required for DOCX + PDF + XLSX skills (documented workflows)
    .apt_install([
        "pandoc",           # DOCX → Markdown with tracked changes
        "libreoffice",      # DOCX → PDF, XLSX formula recalculation (mandatory)
        "poppler-utils",    # PDF text/image extraction
        "zip",              # OOXML unpack (DOCX)
        "unzip",            # OOXML pack (DOCX)
        "python3",          # All scripting workflows
        "coreutils",        # grep, cat, etc. explicitly referenced
    ])
    # Optional but documented (PDF Skill - CLI alternatives)
    .apt_install([
        "qpdf",             # PDF operations (CLI alternative)
        "pdftk",            # PDF operations (CLI alternative)
    ])
    # Conditional (PDF OCR workflows only)
    .apt_install([
        "tesseract-ocr",    # OCR for scanned PDFs (only if OCR requested)
    ])
    # Build tools and utilities
        .apt_install([
            "build-essential",
            "git",
            "rsync",  # For efficient file syncing
        ])
    # Workspace isolation and utilities
    .apt_install([
        "bubblewrap",    # Filesystem isolation (mount workspace as /)
        "ripgrep",       # Fast file search (rg) - useful for grep operations
    ])
    # ============================================================================
    # Python Package Manager (uv)
    # ============================================================================
    # Install uv (fast Python package manager) - run as root
    # Install to system-wide location so all users can access it
    .run_cmd("curl -LsSf https://astral.sh/uv/install.sh | CARGO_HOME=/usr/local/cargo sh", user="root")
    .run_cmd("cp /root/.cargo/bin/uv /usr/local/bin/uv 2>/dev/null || cp /root/.local/bin/uv /usr/local/bin/uv 2>/dev/null || true", user="root")
    .run_cmd("chmod +x /usr/local/bin/uv", user="root")
    # ============================================================================
    # Node.js / JavaScript Dependencies
    # ============================================================================
    # Install Node.js and npm (required for DOCX skill - docx-js)
    .run_cmd(
        [
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs",
        ],
        user="root"
    )
    # Install bun (fast Node.js package manager and runtime)
    # Install to system-wide location so all users can access it
    .run_cmd("curl -fsSL https://bun.sh/install | BUN_INSTALL=/usr/local bash", user="root")
    .run_cmd("chmod +x /usr/local/bin/bun", user="root")
    .npm_install(["docx"])
    # Note: docx will be installed per-thread using bun for better isolation
    # ============================================================================
    # Python Dependencies (pip)
    # ============================================================================
    # Required across all three skills (DOCX + PDF + XLSX)
    .pip_install([
        "lxml",
        "python-docx",
        # DOCX dependencies
        "defusedxml",       # Secure OOXML parsing (DOCX)
        # PDF dependencies
        "pypdf",            # Merge, split, rotate, encrypt, metadata (PDF)
        "pdfplumber",       # Text + table extraction (PDF)
        "reportlab",        # PDF generation (PDF)
        # XLSX dependencies
        "openpyxl",         # Create/edit spreadsheets, formulas, formatting (XLSX)
        # Shared dependencies
        "pandas",           # Data analysis, Excel IO, table handling (XLSX, PDF)
    ])
    # Optional / Conditional (PDF OCR & table export)
    .pip_install([
        "pytesseract",      # OCR wrapper (only if OCR workflows invoked)
        "pdf2image",        # PDF to image conversion for OCR (only if OCR workflows invoked)
    ])
    # Additional utilities
    .pip_install([
        "Pillow",           # Image processing (used by various skills)
    ])
    # ============================================================================
    # FUSE Configuration
    # ============================================================================
    # Configure fuse to allow non-root users (needed for rclone with allow_other)
    # This allows the user to mount filesystems that other users can access
    .run_cmd("echo 'user_allow_other' >> /etc/fuse.conf", user="root")
    # ============================================================================
    # Environment Variables
    # ============================================================================
    # Set environment variables for PATH to include uv, rclone and other tools
    # (env_vars dictionary is built above, filtering out None values)
    .set_envs(env_vars)
    # ============================================================================
    # Rclone Configuration Directory
    # ============================================================================
    # Create directory for rclone config (config will be created at runtime)
    .run_cmd("mkdir -p /root/.config/rclone", user="root")
    # ============================================================================
    # Start Command - Rclone S3 Mounts
    # ============================================================================
    # Using rclone instead of mountpoint because:
    # 1. Mountpoint for S3 is AWS-native and has issues with Supabase S3 endpoints
    # 2. Rclone fully supports S3-compatible storage with custom endpoints
    # 3. Proven to work with Supabase storage
    .set_start_cmd(
        """
        # Note: S3 mounting is now handled by sandbox_backend.py after sandbox creation
        # This start command just prepares the mount point directories
        sudo mkdir -p /root/.config/rclone
        echo "[Template] Sandbox initialized (mounts will be configured by backend)"
        """,
        wait_for_timeout(2_000)
    )
)