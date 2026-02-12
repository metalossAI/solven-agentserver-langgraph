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
  compraventa/
    compraventa.md
assets/
    compraventa/
      template.docx
scripts/
  (inicialmente vacío)
```

### references/

Contiene documentos de referencia con carácter autoritativo, con instrucciones detalladas de redacción para cada tipo de escritura.

* Cada subcarpeta corresponde a un tipo concreto de documento notarial.
* Ejemplo:

  * `references/compraventa/compraventa.md`: normas de redacción, explicación de cláusulas, secciones obligatorias y limitaciones legales específicas de las escrituras de compraventa.

Estos documentos **deben cargarse en contexto** y seguirse estrictamente al trabajar con la escritura correspondiente.

### assets/

Contiene recursos como las plantillas oficiales para cada tipo de escritura. Estos son proporcionados por el usuario. Si necesitas algun modelo notarial que no encuentras pidelo.

* Las plantillas constituyen la única fuente válida para la estructura y el orden de las secciones.
El asistente nunca debe inventar una estructura que entre en conflicto con la plantilla.

### scripts/

Inicialmente vacío. Aqui podrias crear nuevos scripts que puedan servir en un futuro. Hazlo solo si es estrictamente necesario y puede ser reutilizado en futuras ocasiones.

## Flujo de trabajo

Debe seguirse el siguiente flujo:

1. **Identificar el tipo de escritura**
  * Ejemplo: compraventa, poder, hipoteca, acta, etc.
2. **Buscar instrucciones para esta escritura** si estan disponibles `references/.md`.
3. **Seleccionar el modelo adecuada** desde `assets/`.
4. **Copiar el modelo a rellenar** desde `assets/`
5. **Editar la plantilla usando la habilidad de docx** usando el workflow de redlining (DOCX).

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
