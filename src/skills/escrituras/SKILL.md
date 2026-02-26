---
name: escrituras
description: relleno, edición y validación de documentos notariales españoles (escrituras públicas) utilizando modelos.
---
# Escrituras

## Visión general

Esta skill permite trabajar con modelos de escrituras notariales, combinando referencias específicas por tipo de documento con plantillas. Todo el trabajo debe partir de las plantillas proporcionadas y ajustarse a estas sin modificar su estructura o formato, siempre y cuando no se pida.

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

1. **Identificar el tipo de escritura** (compraventa, acta, ...)
2. **Buscar instrucciones adicionales para el modelo** si estan disponibles en /references.
3. **Buscar exhaustivamente y seleccionar el modelo adecuado** en `assets/`.
4. **Copiar el modelo a rellenar** desde `assets/` a /worspace
5. **Editar el modelo con el metodo de relleno**

### Metodo de relleno de modelos (pagina a pagina)
#### Pasos
1. Identificar el tipo de archivo del modelo.
2. Cargar el skill para trabajar con el tipo de archivo identificado.
3. Utilizando el skill crear o ejecutar scripts para trabajar pagina por pagina.
4. Leer el archivo
5. Analizar campos a rellenar
6. Rellenar cada campo identificado de forma precisa

> Ejemplo
> El usuario pide crear un tipo de documento, encuentras el modelo dentro de .solven/skills/escrituras/assets, identificas si es un pdf o docx,  analizas la estructura del documento, identificas los campos a rellenar, lees el skill para el tipo de documento (docx, o pdf), llevas a cabo la ejecucion de comandos y scripts correspondientes para rellenar el modelo con precision.

#### Reglas
- No cambiar el formato del modelo si el usuario no lo pide explicitamente.
- No cambiar la estructura del modelo si el usuario no lo pide explicitamente.
- Evita rellenar un modelo de una sola pasada, esto resultara en un modelo incompleto.
- Usar comentarios y redlining para indicar cambios al editar escrituras
- Trabajar siempre con plantillas.
- Trabajar sobre el mismo archivo evitando versionados.


## Ejemplos de activación de esta skill
- "Redactar una escritura de compraventa utilizando la plantilla estándar."
- "Editar la compraventa para modificar el precio y la forma de pago."
- "Cumplimentar el PDF final de compraventa con los términos acordados."
- "Validar este PDF firmado frente a la plantilla DOCX original."
- "Genera la escritura"
- "Incluye este documento unido"
