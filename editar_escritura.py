#!/usr/bin/env python3
"""
Script para editar la plantilla de Escritura de Compraventa de Vivienda.
Completa campos marcados como [A COMPLETAR] con información de ejemplo.
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
sys.path.insert(0, str(skill_root.parent))

from scripts.document import Document

def main():
    # Ruta al directorio descomprimido
    unpacked_dir = "escritura_unpacked"
    
    if not os.path.exists(unpacked_dir):
        print(f"Error: El directorio '{unpacked_dir}' no existe.")
        print("Primero debes descomprimir el documento:")
        print(f'  unzip "Escritura Compraventa Vivienda.docx" -d {unpacked_dir}')
        sys.exit(1)
    
    # Inicializar el documento
    print(f"Inicializando documento desde: {unpacked_dir}")
    doc = Document(unpacked_dir, author="Editor", initials="ED", track_revisions=True)
    
    # Edición 1: Completar el Protocolo
    print("\n1. Completando campo Protocolo...")
    # Buscar el párrafo que contiene "Protocolo"
    para = doc["word/document.xml"].get_node(tag="w:p", contains="Protocolo")
    if para:
        # Buscar el run que contiene "[A COMPLETAR]"
        runs = para.getElementsByTagName("w:r")
        for run in runs:
            text_nodes = run.getElementsByTagName("w:t")
            for text_node in text_nodes:
                if "[A COMPLETAR]" in text_node.firstChild.nodeValue:
                    # Obtener formato
                    tags = run.getElementsByTagName("w:rPr")
                    rpr = tags[0].toxml() if tags else ""
                    rsid = run.getAttribute("w:rsidR") if run.hasAttribute("w:rsidR") else "00AB12CD"
                    
                    replacement = f'''<w:r w:rsidR="{rsid}">{rpr}<w:t xml:space="preserve"> </w:t></w:r>
<w:del><w:r>{rpr}<w:delText>[A COMPLETAR]</w:delText></w:r></w:del>
<w:ins><w:r>{rpr}<w:t>2025/1234</w:t></w:r></w:ins>'''
                    
                    doc["word/document.xml"].replace_node(run, replacement)
                    print("   ✓ Campo Protocolo completado")
                    break
        else:
            print("   ⚠ No se encontró [A COMPLETAR] en el campo Protocolo")
    else:
        print("   ⚠ No se encontró el párrafo con Protocolo")
    
    # Edición 2: Completar el Notario
    print("\n2. Completando campo Notario...")
    # Buscar párrafo que contiene "Notario:" seguido de "[A COMPLETAR]"
    paras = doc["word/document.xml"].dom.getElementsByTagName("w:p")
    para = None
    for p in paras:
        text_content = "".join([t.firstChild.nodeValue if t.firstChild else "" 
                                for t in p.getElementsByTagName("w:t")])
        if "Notario:" in text_content and "[A COMPLETAR]" in text_content:
            para = p
            break
    
    if para:
        runs = para.getElementsByTagName("w:r")
        for run in runs:
            text_nodes = run.getElementsByTagName("w:t")
            for text_node in text_nodes:
                if "[A COMPLETAR]" in text_node.firstChild.nodeValue:
                    tags = run.getElementsByTagName("w:rPr")
                    rpr = tags[0].toxml() if tags else ""
                    rsid = run.getAttribute("w:rsidR") if run.hasAttribute("w:rsidR") else "00AB12CD"
                    
                    replacement = f'''<w:r w:rsidR="{rsid}">{rpr}<w:t xml:space="preserve"> </w:t></w:r>
<w:del><w:r>{rpr}<w:delText>[A COMPLETAR]</w:delText></w:r></w:del>
<w:ins><w:r>{rpr}<w:t>D. Miguel Ángel Torres Pérez</w:t></w:r></w:ins>'''
                    
                    doc["word/document.xml"].replace_node(run, replacement)
                    print("   ✓ Campo Notario completado")
                    break
        else:
            print("   ⚠ No se encontró [A COMPLETAR] en el campo Notario")
    else:
        print("   ⚠ No se encontró el párrafo con Notario")
    
    # Edición 3: Completar la Fecha
    print("\n3. Completando campo Fecha...")
    paras = doc["word/document.xml"].dom.getElementsByTagName("w:p")
    para = None
    for p in paras:
        text_content = "".join([t.firstChild.nodeValue if t.firstChild else "" 
                                for t in p.getElementsByTagName("w:t")])
        if "Fecha:" in text_content and "[A COMPLETAR" in text_content:
            para = p
            break
    
    if para:
        runs = para.getElementsByTagName("w:r")
        for run in runs:
            text_nodes = run.getElementsByTagName("w:t")
            for text_node in text_nodes:
                if "[A COMPLETAR" in text_node.firstChild.nodeValue:
                    tags = run.getElementsByTagName("w:rPr")
                    rpr = tags[0].toxml() if tags else ""
                    rsid = run.getAttribute("w:rsidR") if run.hasAttribute("w:rsidR") else "00AB12CD"
                    
                    replacement = f'''<w:r w:rsidR="{rsid}">{rpr}<w:t xml:space="preserve"> </w:t></w:r>
<w:del><w:r>{rpr}<w:delText>[A COMPLETAR - próxima a 12 de diciembre de 2025]</w:delText></w:r></w:del>
<w:ins><w:r>{rpr}<w:t>15 de diciembre de 2025</w:t></w:r></w:ins>'''
                    
                    doc["word/document.xml"].replace_node(run, replacement)
                    print("   ✓ Campo Fecha completado")
                    break
        else:
            print("   ⚠ No se encontró [A COMPLETAR] en el campo Fecha")
    else:
        print("   ⚠ No se encontró el párrafo con Fecha")
    
    # Edición 4: Completar Código Postal
    print("\n4. Completando campo Código Postal...")
    paras = doc["word/document.xml"].dom.getElementsByTagName("w:p")
    para = None
    for p in paras:
        text_content = "".join([t.firstChild.nodeValue if t.firstChild else "" 
                                for t in p.getElementsByTagName("w:t")])
        if "CÓDIGO POSTAL:" in text_content and "[A COMPLETAR]" in text_content:
            para = p
            break
    
    if para:
        runs = para.getElementsByTagName("w:r")
        for run in runs:
            text_nodes = run.getElementsByTagName("w:t")
            for text_node in text_nodes:
                if "[A COMPLETAR]" in text_node.firstChild.nodeValue:
                    tags = run.getElementsByTagName("w:rPr")
                    rpr = tags[0].toxml() if tags else ""
                    rsid = run.getAttribute("w:rsidR") if run.hasAttribute("w:rsidR") else "00AB12CD"
                    
                    replacement = f'''<w:r w:rsidR="{rsid}">{rpr}<w:t xml:space="preserve"> </w:t></w:r>
<w:del><w:r>{rpr}<w:delText>[A COMPLETAR]</w:delText></w:r></w:del>
<w:ins><w:r>{rpr}<w:t>46001</w:t></w:r></w:ins>'''
                    
                    doc["word/document.xml"].replace_node(run, replacement)
                    print("   ✓ Campo Código Postal completado")
                    break
        else:
            print("   ⚠ No se encontró [A COMPLETAR] en el campo Código Postal")
    else:
        print("   ⚠ No se encontró el párrafo con CÓDIGO POSTAL")
    
    # Edición 5: Completar domicilio de la sociedad
    print("\n5. Completando domicilio de la sociedad...")
    paras = doc["word/document.xml"].dom.getElementsByTagName("w:p")
    para = None
    for p in paras:
        text_content = "".join([t.firstChild.nodeValue if t.firstChild else "" 
                                for t in p.getElementsByTagName("w:t")])
        if "domiciliada en" in text_content and "[A COMPLETAR]" in text_content:
            para = p
            break
    
    if para:
        runs = para.getElementsByTagName("w:r")
        for run in runs:
            text_nodes = run.getElementsByTagName("w:t")
            for text_node in text_nodes:
                if "[A COMPLETAR]" in text_node.firstChild.nodeValue:
                    tags = run.getElementsByTagName("w:rPr")
                    rpr = tags[0].toxml() if tags else ""
                    rsid = run.getAttribute("w:rsidR") if run.hasAttribute("w:rsidR") else "00AB12CD"
                    
                    replacement = f'''<w:r w:rsidR="{rsid}">{rpr}<w:t>domiciliada en </w:t></w:r>
<w:del><w:r>{rpr}<w:delText>[A COMPLETAR]</w:delText></w:r></w:del>
<w:ins><w:r>{rpr}<w:t>Calle Mayor, 45, 46001 Valencia</w:t></w:r></w:ins>'''
                    
                    doc["word/document.xml"].replace_node(run, replacement)
                    print("   ✓ Domicilio de la sociedad completado")
                    break
        else:
            print("   ⚠ No se encontró [A COMPLETAR] en el campo de domicilio")
    else:
        print("   ⚠ No se encontró el párrafo con domicilio")
    
    # Edición 6: Agregar un comentario
    print("\n6. Agregando comentario...")
    para = doc["word/document.xml"].get_node(tag="w:p", contains="Protocolo")
    if para:
        doc.add_comment(start=para, end=para, text="Este campo fue completado mediante script automatizado")
        print("   ✓ Comentario agregado")
    else:
        print("   ⚠ No se encontró el párrafo para comentar")
    
    # Guardar el documento
    print("\n7. Guardando cambios...")
    try:
        doc.save(validate=True)
        print("   ✓ Documento guardado exitosamente")
    except ValueError as e:
        print(f"   ⚠ Error de validación: {e}")
        print("   Guardando sin validación para continuar...")
        doc.save(validate=False)
        print("   ✓ Documento guardado (sin validación)")
    
    # Empaquetar el documento editado
    print("\n8. Empaquetando documento final...")
    from ooxml.scripts.pack import pack_document
    output_file = "Escritura Compraventa Vivienda - Editada.docx"
    pack_document(unpacked_dir, output_file, validate=False)
    print(f"   ✓ Documento empaquetado: {output_file}")
    
    print("\n✅ Edición de la escritura completada exitosamente!")
    print("\nNota: Los cambios aparecerán como tracked changes (sugerencias) en Word.")

if __name__ == "__main__":
    main()

