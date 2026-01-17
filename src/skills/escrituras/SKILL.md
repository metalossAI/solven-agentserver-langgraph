---
name: escrituras
description: Esta skill para la creación, edición y validación de documentos notariales españoles (escrituras públicas) utilizando plantillas autorizadas y flujos de trabajo específicos según el tipo de documento.
---
# Escrituras

## Visión general

Esta skill permite la creación y modificación estructurada y jurídicamente coherente de documentos notariales españoles, combinando referencias específicas por tipo de documento con plantillas controladas. Todo el trabajo debe partir de las plantillas proporcionadas y todas las modificaciones deben ajustarse a las convenciones de redacción notarial españolas.

## Estructura de la skill y organización de recursos

Esta skill se basa en una estructura de directorios fija. El asistente debe comprender y respetar la función de cada directorio.

```
SKILL.md
references/
  compraventa/
    compraventa.md
assets/
  templates/
    compraventa.docx
    compraventa.pdf
scripts/
  (inicialmente vacío)
```

### references/

Contiene documentos de referencia con carácter autoritativo, con instrucciones detalladas de redacción para cada tipo de escritura.

* Cada subcarpeta corresponde a un tipo concreto de documento notarial.
* Ejemplo:

  * `references/compraventa/compraventa.md`: normas de redacción, explicación de cláusulas, secciones obligatorias y limitaciones legales específicas de las escrituras de compraventa.

Estos documentos **deben cargarse en contexto** y seguirse estrictamente al trabajar con la escritura correspondiente.

### assets/templates/

Contiene las plantillas oficiales para cada tipo de escritura.

* Las plantillas pueden proporcionarse en:

  * DOCX (plantilla editable para redacción)
  * PDF (plantilla final o basada en formularios)
* Las plantillas constituyen la única fuente válida para la estructura y el orden de las secciones.

El asistente nunca debe inventar una estructura que entre en conflicto con la plantilla.

### assets/sello/sello.*

Contiene el sello del notario que se usa para cada escritura.

### scripts/

Inicialmente vacío. Este directorio queda reservado para futuros scripts de automatización relacionados con el procesamiento de documentos DOCX o PDF.

## Árbol de decisión del flujo de trabajo

Cuando se invoque esta skill, debe seguirse el siguiente flujo:

1. **Identificar el tipo de escritura**
  * Ejemplo: compraventa, poder, hipoteca, acta, etc.
2. **Cargar el documento de referencia correspondiente** desde `references/`.
3. **Seleccionar la plantilla adecuada** desde `assets/templates/`.
4. **Determinar el formato de trabajo**:

  * **Flujo DOCX**: redacción, edición, modificación de cláusulas o cambios iterativos.
  * **Flujo PDF**: documentos finalizados, cumplimentación de formularios, extracción o validación.
5. **Cargar la habilidad de documento adecuada** (DOCX o PDF).
6. **Validar conforme a los estándares notariales** antes de generar el resultado.

## Flujo DOCX (redacción y edición)

Utilizar este flujo cuando se cree o modifique el contenido de una escritura.

Responsabilidades:

* Incorporar datos de las partes, capacidades, datos del inmueble, precios e identificadores legales.
* Editar o adaptar cláusulas siguiendo estrictamente el documento de referencia.
* Añadir documentos unidos al final de la escritura.
* Mantener el lenguaje y la sintaxis propios del estilo notarial español.

Toda la redacción debe realizarse utilizando la plantilla DOCX como base.

## Flujo PDF (documentos finalizados)

Utilizar este flujo al trabajar con documentos finalizados o próximos a su versión final.

Responsabilidades:

* Cumplimentar los campos predefinidos del PDF cuando proceda.
* Extraer texto para revisión o comparación.
* Validar que el contenido del PDF coincide con la plantilla DOCX y con las reglas del documento de referencia.

Los flujos PDF nunca deben introducir nuevo lenguaje jurídico.

## Reglas de validación

Antes de entregar el resultado, el asistente debe:

* Garantizar la coherencia de nombres, fechas, identificadores e importes.
* Confirmar la adecuación al documento de referencia del tipo de escritura.
* Verificar que no falte ninguna cláusula o sección obligatoria.
* Mantener un tono formal, neutro y jurídicamente preciso.

## Ejemplos de activación de esta skill

* "Redactar una escritura de compraventa utilizando la plantilla estándar."
* "Editar la compraventa para modificar el precio y la forma de pago."
* "Cumplimentar el PDF final de compraventa con los términos acordados."
* "Validar este PDF firmado frente a la plantilla DOCX original."
* "Genera la escritura"
* "Incluye este documento unido"

Esta skill debe priorizar siempre la corrección jurídica, la estructura formal y el cumplimiento de la práctica notarial española.
