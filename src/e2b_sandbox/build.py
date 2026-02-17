# build_prod.py
from dotenv import load_dotenv
from e2b import Template, default_build_logger
from template import template
import os

load_dotenv()

if __name__ == '__main__':
    
    Template.build(
        template,
        alias="solven-sandbox-v1",
        cpu_count=4,
        memory_mb=4096,
        on_build_logs=default_build_logger(),
    )