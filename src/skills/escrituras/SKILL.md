---
name: escrituras
description: Esta skill para la creación, edición y validación de documentos notariales españoles (escrituras públicas) utilizando plantillas autorizadas y flujos de trabajo específicos según el tipo de documento.
---
# Escrituras

## Visión general

Esta skill permite la creación y modificación estructurada y jurídicamente coherente de documentos notariales españoles, combinando referencias específicas por tipo de documento con plantillas controladas. Todo el trabajo debe partir de las plantillas proporcionadas y ajustarse a estas.

## Estructura de la skill y organización de recursos
Trabajar en una escritura requiere tener disponible al menos una plantilla o modelo.

El usuario ha dejado a tu disposición las plantillas en assets/; si no encuentras el modelo requerido para crear una escritura hazlo saber y solicitalo.

En /references puedes encontrar instrucciones segun el tipo de escritura; aunque es opcional, por lo tanto si no encuentras instrucciones especificas a la escritura que se te pide, puedes pedir al usuario que te las proporcione si quiere.

```
SKILL.md
references/
assets/
scripts/
  (inicialmente vacío)
```

## Flujo de trabajo

Debe seguirse el siguiente flujo:

1. **Identificar el tipo de escritura**
  * Ejemplo: compraventa, poder, hipoteca, acta, etc.
2. **Buscar instrucciones adicionales para el modelo** si estan disponibles en /references.
3. **Buscar y seleccionar el modelo adecuada** desde `assets/`.
4. **Copiar el modelo a rellenar** desde `assets/` a /worspace
5. **Editar la plantilla usando la habilidad de docx**

### Reglas de edición

* Usar comentarios y redlining para indicar cambios al editar escrituras
* Mantener el estilo original cuando se trabaje sobre plantillas

## Reglas de validación

Antes de entregar el resultado, el asistente debe:
* Garantizar la coherencia de nombres, fechas, identificadores e importes.
* Confirmar la adecuación al documento de referencia del tipo de escritura.
* Verificar que no falte ninguna cláusula o sección obligatoria.

## Ejemplos de activación de esta skill

* "Redactar una escritura de compraventa utilizando la plantilla estándar."
* "Editar la compraventa para modificar el precio y la forma de pago."
* "Cumplimentar el PDF final de compraventa con los términos acordados."
* "Validar este PDF firmado frente a la plantilla DOCX original."
* "Genera la escritura"
* "Incluye este documento unido"
