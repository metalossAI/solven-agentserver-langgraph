---
name: escrituras
description: Esta skill para la creación, edición y validación de documentos notariales españoles (escrituras públicas) utilizando plantillas autorizadas y flujos de trabajo específicos según el tipo de documento.
---
# Escrituras

## Visión general

Esta skill permite la creación y modificación estructurada y jurídicamente coherente de documentos notariales españoles, combinando referencias específicas por tipo de documento con plantillas controladas. Todo el trabajo debe partir de las plantillas proporcionadas y ajustarse a estas.

## Estructura de la skill y organización de recursos

```
SKILL.md
references/
assets/
scripts/
```
### /assets
El usuario ha dejado a tu disposición las plantillas en assets/, aqui deberas buscar modelos relacionados con la escritura correspondiente.

En /references puedes encontrar instrucciones segun el tipo de escritura; aunque es opcional, por lo tanto si no encuentras instrucciones especificas a la escritura que se te pide, puedes pedir al usuario que te las proporcione si quiere.

## Flujo de trabajo

Debe seguirse el siguiente flujo:

1. **Identificar el tipo de escritura**
  * Ejemplo: compraventa, poder, hipoteca, acta, etc.
2. **Buscar instrucciones adicionales para el modelo** si estan disponibles en /references.
3. **Buscar exhaustivamente y seleccionar el modelo adecuado** en `assets/`.
4. **Copiar el modelo a rellenar** desde `assets/` a /worspace
5. **Editar el modelo con el metodo de relleno**

### Metodo de relleno de modelos (pagina a pagina)
#### pasos
1. Identificar el tipo de archivo del modelo.
2. Cargar el skill para trabajar con el tipo de archivo identificado.
3. Utilizando el skill crear o ejecutar scripts para trabajar pagina por pagina.
#### reglas
- Evita rellenar un modelo de una sola pasada, esto resultara en un modelo incompleto.
- Usar comentarios y redlining para indicar cambios al editar escrituras
- Trabajar siempre con plantillas.
- Trabajar sobre el mismo archivo evitando versionados.

## Reglas de validació


## Ejemplos de activación de esta skill

- "Redactar una escritura de compraventa utilizando la plantilla estándar."
- "Editar la compraventa para modificar el precio y la forma de pago."
- "Cumplimentar el PDF final de compraventa con los términos acordados."
- "Validar este PDF firmado frente a la plantilla DOCX original."
- "Genera la escritura"
- "Incluye este documento unido"
