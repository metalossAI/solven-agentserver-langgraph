"""
Workspace Template Management System

Provides template-based workspace initialization for consistent,
fast, and reliable agent execution environments.
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional
import yaml


class WorkspaceTemplate:
    """Manages workspace templates for quick initialization."""
    
    # Template directory (relative to project root)
    TEMPLATE_DIR = Path(__file__).parent.parent / "workspace-templates"
    
    @classmethod
    def list_templates(cls) -> List[str]:
        """List available workspace templates."""
        if not cls.TEMPLATE_DIR.exists():
            return []
        
        templates = []
        for file in cls.TEMPLATE_DIR.glob("*.yaml"):
            templates.append(file.stem)
        
        return sorted(templates)
    
    @classmethod
    def load_template(cls, name: str = "default") -> Dict:
        """
        Load template configuration from YAML.
        
        Args:
            name: Template name (e.g., "default", "data-science", "minimal")
            
        Returns:
            Dictionary with template configuration
            
        Raises:
            FileNotFoundError: If template doesn't exist
        """
        template_path = cls.TEMPLATE_DIR / f"{name}.yaml"
        
        if not template_path.exists():
            available = cls.list_templates()
            raise FileNotFoundError(
                f"Template '{name}' not found. "
                f"Available templates: {', '.join(available)}"
            )
        
        with open(template_path, 'r') as f:
            template = yaml.safe_load(f)
        
        return template
    
    @classmethod
    def create_pyproject_toml(cls, template: Dict) -> str:
        """
        Generate pyproject.toml content from template.
        
        Args:
            template: Template configuration dictionary
            
        Returns:
            pyproject.toml content as string
        """
        python_config = template.get('python', {})
        version = python_config.get('version', '3.12')
        dependencies = python_config.get('dependencies', [])
        
        # Build pyproject.toml
        pyproject = {
            "project": {
                "name": "workspace",
                "version": "0.1.0",
                "description": f"Workspace from template: {template.get('name', 'unknown')}",
                "requires-python": f">={version}",
                "dependencies": dependencies
            },
            "tool": {
                "uv": {
                    "dev-dependencies": []
                }
            }
        }
        
        # Convert to TOML format manually (simple approach)
        toml_lines = [
            "[project]",
            f'name = "workspace"',
            f'version = "0.1.0"',
            f'description = "Workspace from template: {template.get("name", "unknown")}"',
            f'requires-python = ">={version}"',
            'dependencies = [',
        ]
        
        for dep in dependencies:
            toml_lines.append(f'    "{dep}",')
        
        toml_lines.append(']')
        toml_lines.append('')
        toml_lines.append('[tool.uv]')
        toml_lines.append('dev-dependencies = []')
        
        return '\n'.join(toml_lines)
    
    @classmethod
    def create_package_json(cls, template: Dict) -> str:
        """
        Generate package.json content from template.
        
        Args:
            template: Template configuration dictionary
            
        Returns:
            package.json content as string
        """
        nodejs_config = template.get('nodejs', {})
        dependencies = nodejs_config.get('dependencies', {})
        
        package_json = {
            "name": "workspace",
            "version": "1.0.0",
            "type": "module",
            "description": f"Workspace from template: {template.get('name', 'unknown')}",
            "dependencies": dependencies
        }
        
        return json.dumps(package_json, indent=2)
    
    @classmethod
    def create_gitignore(cls) -> str:
        """Generate standard .gitignore for workspace."""
        return """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
env/
venv/
.pytest_cache/
.mypy_cache/
*.egg-info/

# Node
node_modules/
.npm/
*.log

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.local

# Cache
.cache/
*.cache

# Output
*.png
*.jpg
*.pdf
*.xlsx
*.docx
"""
    
    @classmethod
    def create_readme(cls, template: Dict) -> str:
        """Generate README for workspace."""
        name = template.get('name', 'unknown')
        description = template.get('description', 'No description')
        python_version = template.get('python', {}).get('version', '3.12')
        nodejs_version = template.get('nodejs', {}).get('version', '20')
        
        readme = f"""# Workspace

**Template**: {name}  
**Description**: {description}

## Environment

- **Python**: {python_version}
- **Node.js**: {nodejs_version}
- **Runtime**: Bun

## Python Packages

"""
        
        # List Python packages
        python_deps = template.get('python', {}).get('dependencies', [])
        if python_deps:
            for dep in python_deps:
                readme += f"- {dep}\n"
        else:
            readme += "- None\n"
        
        readme += "\n## Node.js Packages\n\n"
        
        # List Node packages
        node_deps = template.get('nodejs', {}).get('dependencies', {})
        if node_deps:
            for pkg, version in node_deps.items():
                readme += f"- {pkg}@{version}\n"
        else:
            readme += "- None\n"
        
        readme += """
## Usage

This workspace is automatically configured and ready to use.

### Python
```python
# Python packages are pre-installed in the virtual environment
# Import and use them directly
import pandas as pd
import matplotlib.pyplot as plt
```

### Node.js
```javascript
// Node packages are pre-installed in node_modules
// Import and use them directly
import axios from 'axios';
import { Document, Packer } from 'docx';
```

## File Operations

All file operations are sandboxed and isolated. Files created in this workspace
persist across sessions and are stored securely.

---

*This workspace was automatically generated and configured.*
"""
        
        return readme
    
    @classmethod
    def get_template_info(cls, name: str) -> Dict:
        """
        Get information about a template without loading full config.
        
        Args:
            name: Template name
            
        Returns:
            Dictionary with template info (name, description, packages count)
        """
        try:
            template = cls.load_template(name)
            
            python_deps = len(template.get('python', {}).get('dependencies', []))
            node_deps = len(template.get('nodejs', {}).get('dependencies', {}))
            
            return {
                "name": name,
                "version": template.get('version', 'unknown'),
                "description": template.get('description', ''),
                "python_packages": python_deps,
                "node_packages": node_deps,
                "python_version": template.get('python', {}).get('version', 'unknown'),
                "nodejs_version": template.get('nodejs', {}).get('version', 'unknown'),
            }
        except FileNotFoundError:
            return {
                "name": name,
                "error": "Template not found"
            }


# Example usage
if __name__ == "__main__":
    # List available templates
    templates = WorkspaceTemplate.list_templates()
    print("Available templates:", templates)
    
    # Load default template
    template = WorkspaceTemplate.load_template("default")
    print(f"\nTemplate: {template['name']}")
    print(f"Description: {template['description']}")
    
    # Generate configuration files
    pyproject = WorkspaceTemplate.create_pyproject_toml(template)
    print("\n=== pyproject.toml ===")
    print(pyproject)
    
    package_json = WorkspaceTemplate.create_package_json(template)
    print("\n=== package.json ===")
    print(package_json)
    
    # Get template info
    for tmpl_name in templates:
        info = WorkspaceTemplate.get_template_info(tmpl_name)
        print(f"\n{info['name']}: {info['python_packages']} Python + {info['node_packages']} Node packages")

