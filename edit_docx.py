#!/usr/bin/env python3
"""
Script para editar un documento DOCX usando la Document library.
Primero debe descomprimirse el documento con: python ooxml/scripts/unpack.py documento_ejemplo.docx unpacked
"""

import sys
import os
from pathlib import Path

# Encontrar la raíz del skill docx
def find_docx_skill_root():
    """Busca la raíz del skill docx (directorio que contiene scripts/ y ooxml/)"""
    current_dir = Path(__file__).parent.absolute()
    
    # Buscar en el directorio actual y padres
    for path in [current_dir] + list(current_dir.parents):
        scripts_dir = path / "src" / "skills" / "docx" / "scripts"
        if scripts_dir.exists() and (scripts_dir / "document.py").exists():
            return path / "src" / "skills" / "docx"
    
    # Si no se encuentra, intentar ruta relativa común
    skill_root = current_dir / "src" / "skills" / "docx"
    if (skill_root / "scripts" / "document.py").exists():
        return skill_root
    
    raise FileNotFoundError(
        "No se pudo encontrar el directorio del skill docx. "
        "Asegúrate de que el script esté en el directorio correcto o ajusta PYTHONPATH."
    )

# Configurar PYTHONPATH
skill_root = find_docx_skill_root()
sys.path.insert(0, str(skill_root))

# El módulo ooxml está en skill_root/ooxml, pero se importa como "ooxml"
# Necesitamos agregar el directorio padre al path
sys.path.insert(0, str(skill_root.parent))

from scripts.document import Document

def main():
    # Ruta al directorio descomprimido
    unpacked_dir = "unpacked"
    
    if not os.path.exists(unpacked_dir):
        print(f"Error: El directorio '{unpacked_dir}' no existe.")
        print("Primero debes descomprimir el documento:")
        print(f"  python {skill_root}/ooxml/scripts/unpack.py documento_ejemplo.docx {unpacked_dir}")
        sys.exit(1)
    
    # Inicializar el documento
    print(f"Inicializando documento desde: {unpacked_dir}")
    doc = Document(unpacked_dir, author="Editor", initials="ED")
    
    # Ejemplo 1: Reemplazar texto en un párrafo
    print("\n1. Editando texto en párrafo...")
    node = doc["word/document.xml"].get_node(tag="w:r", contains="texto que será editado")
    if node:
        # Obtener el formato del run original
        tags = node.getElementsByTagName("w:rPr")
        rpr = tags[0].toxml() if tags else ""
        
        # Extraer el RSID del run original si existe
        rsid = node.getAttribute("w:rsidR") if node.hasAttribute("w:rsidR") else "00AB12CD"
        
        # Crear reemplazo con tracked changes
        replacement = f'''<w:r w:rsidR="{rsid}">{rpr}<w:t>texto que ha sido </w:t></w:r>
<w:del><w:r>{rpr}<w:delText>editado</w:delText></w:r></w:del>
<w:ins><w:r>{rpr}<w:t>actualizado exitosamente</w:t></w:r></w:ins>'''
        
        doc["word/document.xml"].replace_node(node, replacement)
        print("   ✓ Texto reemplazado con tracked changes")
    else:
        print("   ⚠ No se encontró el texto a editar")
    
    # Ejemplo 2: Modificar otro párrafo
    print("\n2. Modificando otro párrafo...")
    node = doc["word/document.xml"].get_node(tag="w:r", contains="información importante")
    if node:
        tags = node.getElementsByTagName("w:rPr")
        rpr = tags[0].toxml() if tags else ""
        rsid = node.getAttribute("w:rsidR") if node.hasAttribute("w:rsidR") else "00AB12CD"
        
        replacement = f'''<w:r w:rsidR="{rsid}">{rpr}<w:t>Este párrafo contiene </w:t></w:r>
<w:del><w:r>{rpr}<w:delText>información importante</w:delText></w:r></w:del>
<w:ins><w:r>{rpr}<w:t>datos actualizados y relevantes</w:t></w:r></w:ins>
<w:r w:rsidR="{rsid}">{rpr}<w:t> que necesitamos modificar.</w:t></w:r>'''
        
        doc["word/document.xml"].replace_node(node, replacement)
        print("   ✓ Párrafo modificado")
    else:
        print("   ⚠ No se encontró el párrafo a modificar")
    
    # Ejemplo 3: Agregar un comentario
    print("\n3. Agregando comentario...")
    node = doc["word/document.xml"].get_node(tag="w:r", contains="actualizado exitosamente")
    if node:
        # Buscar el elemento padre (w:ins) para el comentario
        parent = node.parentNode
        while parent and parent.tagName != "w:ins":
            parent = parent.parentNode
        
        if parent:
            doc.add_comment(start=parent, end=parent, text="Este cambio fue realizado mediante script automatizado")
            print("   ✓ Comentario agregado")
    else:
        print("   ⚠ No se encontró el elemento para comentar")
    
    # Guardar el documento
    print("\n4. Guardando cambios...")
    try:
        doc.save(validate=True)
        print("   ✓ Documento guardado exitosamente")
    except ValueError as e:
        print(f"   ⚠ Error de validación: {e}")
        print("   Guardando sin validación para continuar...")
        doc.save(validate=False)
        print("   ✓ Documento guardado (sin validación)")
    
    # Empaquetar el documento editado
    print("\n5. Empaquetando documento final...")
    # pack_document ya está importado desde scripts.document
    from ooxml.scripts.pack import pack_document
    pack_document(unpacked_dir, "documento_editado.docx", validate=True)
    print("   ✓ Documento empaquetado: documento_editado.docx")
    
    print("\n✅ Edición completada exitosamente!")

if __name__ == "__main__":
    main()

