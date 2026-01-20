# Ejemplo: Crear y Editar Documento DOCX

Este ejemplo demuestra cómo crear un documento DOCX desde cero y luego editarlo siguiendo las instrucciones de `src/skills/docx/SKILL.md`.

## Requisitos Previos

1. **Node.js y docx instalado globalmente:**
   ```bash
   npm install -g docx
   ```

2. **Python con las dependencias del skill docx:**
   - `defusedxml`
   - Las librerías del skill docx deben estar disponibles

## Paso 1: Crear el Documento

Ejecuta el script JavaScript para crear un documento DOCX:

```bash
node create_docx.js
```

Esto creará `documento_ejemplo.docx` con:
- Título del documento
- Encabezados (Heading 1 y Heading 2)
- Párrafos de texto que serán editados posteriormente

## Paso 2: Descomprimir el Documento

Antes de editar, necesitas descomprimir el documento DOCX:

```bash
python src/skills/docx/ooxml/scripts/unpack.py documento_ejemplo.docx unpacked
```

Esto creará el directorio `unpacked/` con la estructura XML del documento.

## Paso 3: Editar el Documento

Ejecuta el script Python para editar el documento:

```bash
python edit_docx.py
```

Este script:
1. Inicializa el documento usando la Document library
2. Reemplaza texto con tracked changes (cambios rastreados)
3. Agrega comentarios
4. Guarda los cambios
5. Empaqueta el documento final como `documento_editado.docx`

## Estructura de los Scripts

### `create_docx.js`
- Usa la librería `docx` (docx-js)
- Crea un documento con estilos profesionales
- Exporta el documento como `.docx` usando `Packer.toBuffer()`

### `edit_docx.py`
- Usa la Document library del skill docx
- Implementa tracked changes (cambios rastreados) usando `<w:del>` y `<w:ins>`
- Agrega comentarios al documento
- Guarda y empaqueta el documento editado

## Notas Importantes

1. **Tracked Changes**: El script de edición usa tracked changes, lo que significa que los cambios aparecerán como sugerencias en Word (similar a "Track Changes" en Word).

2. **RSIDs**: Los RSIDs (Revision Session IDs) son generados automáticamente por la Document library.

3. **Formato Preservado**: El script preserva el formato original del texto cuando hace reemplazos.

4. **Validación**: El documento final es validado antes de empaquetarse para asegurar que el XML es válido.

## Personalización

Puedes modificar los scripts para:
- Cambiar el contenido del documento inicial
- Agregar más secciones o elementos
- Implementar diferentes tipos de ediciones
- Agregar imágenes, tablas, o listas

Consulta `src/skills/docx/docx-js.md` y `src/skills/docx/ooxml.md` para más detalles sobre las capacidades disponibles.

