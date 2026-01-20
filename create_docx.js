const { Document, Packer, Paragraph, TextRun, AlignmentType, HeadingLevel } = require('docx');
const fs = require('fs');

// Crear un documento con contenido de ejemplo
const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 24 } } },
    paragraphStyles: [
      { id: "Title", name: "Title", basedOn: "Normal",
        run: { size: 56, bold: true, color: "000000", font: "Arial" },
        paragraph: { spacing: { before: 240, after: 120 }, alignment: AlignmentType.CENTER } },
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, color: "000000", font: "Arial" },
        paragraph: { spacing: { before: 240, after: 240 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, color: "000000", font: "Arial" },
        paragraph: { spacing: { before: 180, after: 180 }, outlineLevel: 1 } }
    ]
  },
  sections: [{
    properties: { page: { margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    children: [
      new Paragraph({ heading: HeadingLevel.TITLE, children: [new TextRun("Documento de Ejemplo")] }),
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Introducción")] }),
      new Paragraph({ children: [new TextRun("Este es un documento de ejemplo creado con docx-js.")] }),
      new Paragraph({ children: [new TextRun("Contiene texto que será editado posteriormente.")] }),
      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("Sección Principal")] }),
      new Paragraph({ children: [new TextRun("Este párrafo contiene información importante que necesitamos modificar.")] }),
      new Paragraph({ children: [new TextRun("El texto original será reemplazado con contenido actualizado.")] })
    ]
  }]
});

// Exportar como .docx
Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("documento_ejemplo.docx", buffer);
  console.log("Documento creado exitosamente: documento_ejemplo.docx");
}).catch(err => {
  console.error("Error al crear el documento:", err);
  process.exit(1);
});

