# Example Skill: Escritura de Compraventa

This is an example of a well-structured skill that follows best practices.

## File Structure

```
user_id/skills/escrituras/compraventa/
├── SKILL.md                           # This file - main skill description
├── disclosure_proceso_detallado.md    # Progressive disclosure: detailed process
├── disclosure_clausulas.md            # Progressive disclosure: detailed clauses
└── resources/
    ├── plantilla_base.pdf             # Base template
    ├── ejemplo_completo.md            # Complete example
    ├── clausulas_especiales.md        # Special clauses library
    └── checklist.md                   # Validation checklist
```

## SKILL.md Content

```markdown
---
name: "Escritura de Compraventa Inmobiliaria"
description: "Generación de escrituras públicas de compraventa de bienes inmuebles conforme a la legislación española"
category: "escrituras"
version: "2.1"
author: "Sistema Solven"
last_updated: "2024-12-29"
---

# Escritura de Compraventa Inmobiliaria

## Descripción General

Esta habilidad te permite generar escrituras públicas de compraventa de bienes inmuebles siguiendo la normativa vigente española. Incluye validaciones legales, cálculos automáticos y clausulado estándar.

## Cuándo Usar Esta Habilidad

Utiliza esta habilidad cuando:
- El usuario solicite redactar una escritura de compraventa
- Se necesite formalizar la venta de una propiedad inmobiliaria
- Haya que preparar documentación para firma notarial
- Se requiera incluir clausulado específico de compraventa

**NO uses esta habilidad para:**
- Contratos privados de compraventa (usa la habilidad `contratos/compraventa_privado`)
- Donaciones inmobiliarias (usa `escrituras/donacion`)
- Permutas (usa `escrituras/permuta`)

## Recursos Disponibles

### Plantillas
- `/skills/escrituras/compraventa/resources/plantilla_base.pdf` - Plantilla base notarial

### Documentación Detallada
- `/skills/escrituras/compraventa/disclosure_proceso_detallado.md` - Proceso completo paso a paso
- `/skills/escrituras/compraventa/disclosure_clausulas.md` - Biblioteca de cláusulas con explicaciones

### Ejemplos y Referencias
- `/skills/escrituras/compraventa/resources/ejemplo_completo.md` - Escritura de ejemplo completa
- `/skills/escrituras/compraventa/resources/clausulas_especiales.md` - Cláusulas para situaciones especiales
- `/skills/escrituras/compraventa/resources/checklist.md` - Lista de validación

## Información Requerida

Antes de generar la escritura, recopila:

### Sobre el Inmueble
- [ ] Dirección completa
- [ ] Referencia catastral
- [ ] Datos registrales (Registro, Tomo, Libro, Folio, Inscripción)
- [ ] Superficie (construida y útil)
- [ ] Descripción (tipo, planta, puerta)

### Sobre las Partes
**Vendedor(es):**
- [ ] Nombre completo y DNI/NIE
- [ ] Estado civil
- [ ] Domicilio
- [ ] Título de propiedad

**Comprador(es):**
- [ ] Nombre completo y DNI/NIE
- [ ] Estado civil
- [ ] Domicilio
- [ ] Régimen económico si procede

### Sobre la Transacción
- [ ] Precio total acordado
- [ ] Forma de pago (efectivo, transferencia, financiación)
- [ ] Gastos y tributos (quién los asume)
- [ ] Fecha de entrega del inmueble
- [ ] Cargas o limitaciones existentes

## Proceso de Generación

### 1. Validación Inicial
```
Verifica que tienes toda la información requerida.
Si falta algo crítico, solicítalo al usuario ANTES de continuar.
```

### 2. Verificaciones Legales
- ✓ Vendedor tiene título de propiedad válido
- ✓ No existen cargas ocultas o limitaciones
- ✓ Inmueble libre de ocupantes (si aplica)
- ✓ Cédula de habitabilidad vigente (si aplica)
- ✓ IBI al corriente de pago

**Para verificaciones detalladas:** Lee `/skills/escrituras/compraventa/resources/checklist.md`

### 3. Estructura de la Escritura

La escritura debe incluir estas secciones en orden:

1. **COMPARECENCIA**
   - Identificación del Notario
   - Fecha y lugar
   - Identificación de comparecientes
   - Capacidad legal

2. **INTERVIENEN**
   - Datos completos del vendedor
   - Datos completos del comprador
   - Representación legal (si aplica)

3. **EXPONEN**
   - Título de propiedad del vendedor
   - Manifestaciones sobre el estado del inmueble
   - Manifestaciones sobre cargas

4. **ESTIPULACIONES**
   - Cláusula de compraventa (objeto y precio)
   - Forma de pago
   - Entrega del inmueble
   - Distribución de gastos
   - Cláusulas especiales (si aplican)

5. **DECLARACIONES FISCALES**
   - Valor real de la transacción
   - Referencia catastral
   - Manifestaciones sobre IVA/ITP

6. **OTORGAMIENTO**
   - Consentimiento de las partes
   - Firma

**Para el clausulado detallado de cada sección:** Lee `/skills/escrituras/compraventa/disclosure_clausulas.md`

### 4. Cláusulas Especiales

Según la situación, incluye cláusulas adicionales:

- **Financiación hipotecaria:** Si el comprador necesita hipoteca
- **Venta con reserva de usufructo:** Si el vendedor retiene el usufructo
- **Vivienda habitual con menores:** Protecciones especiales
- **Inmueble arrendado:** Subrogación del arrendatario
- **Comunidad de propietarios:** Deudas pendientes

**Para ejemplos de cláusulas especiales:** Lee `/skills/escrituras/compraventa/resources/clausulas_especiales.md`

### 5. Cálculos Automáticos

Calcula y verifica:
```
- Precio total
- IVA (si construcción nueva) o ITP (si segunda mano)
- Plusvalía municipal
- Gastos notariales (estimados)
- Gastos registrales (estimados)
```

### 6. Revisión Final

Antes de entregar:
- ✓ Todos los datos son correctos y completos
- ✓ Referencias legales son exactas
- ✓ Cálculos verificados
- ✓ Cláusulas apropiadas incluidas
- ✓ Formato correcto según estándares notariales

## Ejemplo de Uso

**Usuario:** "Necesito una escritura de compraventa para un piso"

**Agente:**
1. Carga esta habilidad: `cargar_habilidad("escrituras/compraventa")`
2. Solicita información requerida (ver lista arriba)
3. Verifica que toda la información es correcta
4. Genera la escritura siguiendo la estructura
5. Si necesitas ejemplos: lee `/skills/escrituras/compraventa/resources/ejemplo_completo.md`
6. Si necesitas cláusulas específicas: lee `/skills/escrituras/compraventa/disclosure_clausulas.md`

## Advertencias Legales

⚠️ **IMPORTANTE:**
- Esta escritura debe ser revisada por un notario
- Los cálculos fiscales son orientativos
- Cada caso puede requerir cláusulas específicas adicionales
- Verifica siempre la normativa autonómica aplicable
- En caso de duda, consulta con el departamento legal

## Información Adicional

Para casos complejos o situaciones especiales:
1. Lee el proceso detallado: `/skills/escrituras/compraventa/disclosure_proceso_detallado.md`
2. Consulta la biblioteca de cláusulas: `/skills/escrituras/compraventa/disclosure_clausulas.md`
3. Revisa el checklist completo: `/skills/escrituras/compraventa/resources/checklist.md`
4. Examina el ejemplo completo: `/skills/escrituras/compraventa/resources/ejemplo_completo.md`

## Actualizaciones

**v2.1 (2024-12-29):**
- Añadidas cláusulas para viviendas con certificado energético
- Actualizado cálculo de ITP según nueva normativa
- Mejoradas validaciones de referencias catastrales

**v2.0 (2024-10-15):**
- Añadido soporte para compraventas con reserva de usufructo
- Nuevas cláusulas de protección para vivienda habitual
- Integración con verificación registral

---

**Skill Path:** `escrituras/compraventa`  
**Category:** Escrituras Notariales  
**Maintenance:** Sistema Solven
```

## Progressive Disclosure Example

### disclosure_proceso_detallado.md

```markdown
# Proceso Detallado - Escritura de Compraventa

Este documento proporciona información detallada sobre cada paso del proceso de generación de escrituras de compraventa.

## 1. Comparecencia - Detalles

### Identificación del Notario
Formato estándar:
```
En [CIUDAD], a [DIA] de [MES] de [AÑO].

Ante mí, [NOMBRE COMPLETO DEL NOTARIO], Notario del Ilustre Colegio de [COMUNIDAD],
con residencia en [CIUDAD], con domicilio profesional en [DIRECCION_NOTARIA].
```

### Identificación de Comparecientes

**Formato para persona física:**
```
COMPARECE:

Don/Doña [NOMBRE_COMPLETO], mayor de edad, con DNI/NIE número [NUMERO], 
con domicilio en [DIRECCION_COMPLETA], [ESTADO_CIVIL].

[Si está casado:]
Casado/a en régimen de [REGIMEN: gananciales/separación de bienes] con 
Don/Doña [NOMBRE_CONYUGE], con DNI/NIE [NUMERO_CONYUGE].
```

### Acreditación de Capacidad
```
A mi juicio, el/la compareciente tiene, según su intervención en este acto, 
capacidad legal suficiente para otorgar la presente escritura, y a tal efecto:

INTERVIENE: En su propio nombre y derecho.

[O si es representante:]
INTERVIENE: En representación de [NOMBRE_REPRESENTADO], según poder otorgado 
ante [NOTARIO] en [CIUDAD] el día [FECHA], bajo el número [NUMERO_PROTOCOLO] 
de su protocolo, bastante a mi juicio para este acto.
```

## 2. Exposiciones - Detalles

[...más contenido detallado...]

```

### disclosure_clausulas.md

```markdown
# Biblioteca de Cláusulas - Compraventa Inmobiliaria

Este documento contiene cláusulas detalladas para diferentes situaciones en escrituras de compraventa.

## Cláusula de Objeto y Precio

### Versión Estándar
```
PRIMERA.- OBJETO Y PRECIO.

El vendedor, por el presente contrato, vende al comprador, con todas las garantías 
legales correspondientes, libre de cargas y gravámenes, salvo los que se especifican, 
y éste compra para sí, el siguiente inmueble:

[DESCRIPCION_DETALLADA_INMUEBLE]

VALORACION Y PRECIO: Las partes valoran el inmueble objeto de compraventa y fijan 
su precio en la cantidad de [PRECIO_LETRAS] EUROS ([PRECIO_NUMEROS] €), que el 
comprador entrega al vendedor en este acto, en metálico/mediante transferencia 
bancaria, sirviendo la presente escritura de carta de pago.
```

### Versión con Pago Aplazado
```
PRIMERA.- OBJETO Y PRECIO.

[...descripción del inmueble...]

PRECIO Y FORMA DE PAGO: El precio total se fija en [PRECIO_TOTAL] euros, que se 
abonará de la siguiente forma:

a) [CANTIDAD_INICIAL] euros, entregados en este acto mediante [FORMA_PAGO_1].
b) [CANTIDAD_RESTANTE] euros, que se abonarán el día [FECHA_PAGO_2] mediante [FORMA_PAGO_2].

El comprador se obliga a pagar las cantidades aplazadas en los plazos indicados, 
devengando un interés de [INTERES]% anual en caso de demora.
```

## Cláusula de Cargas

### Inmueble Libre de Cargas
```
SEGUNDA.- CARGAS.

El vendedor manifiesta que el inmueble objeto de compraventa se halla totalmente 
libre de cargas, gravámenes, arrendamientos, ocupantes, condiciones resolutorias, 
y cualquier otra limitación del dominio, sin que exista persona alguna con derecho 
a usar, habitar o poseer la finca.
```

### Inmueble con Hipoteca a Cancelar
```
SEGUNDA.- CARGAS Y CANCELACIÓN HIPOTECARIA.

El inmueble objeto de esta escritura se encuentra gravado con una hipoteca inscrita 
en el Registro de la Propiedad número [NUM] de [CIUDAD], al Tomo [X], Libro [Y], 
Folio [Z], Inscripción [N], a favor de [ENTIDAD_BANCARIA].

El vendedor se compromete a cancelar dicha hipoteca con el precio de venta, 
destinándose [CANTIDAD] euros del precio a la cancelación inmediata del préstamo 
hipotecario. La cancelación registral se tramitará por el comprador.
```

[...más cláusulas...]

```

## Resources Example

### resources/checklist.md

```markdown
# Checklist de Validación - Escritura de Compraventa

## Pre-requisitos Documentales

### Vendedor
- [ ] DNI/NIE vigente
- [ ] Título de propiedad (escritura anterior)
- [ ] Nota simple registral actualizada (máx. 30 días)
- [ ] Certificado de cargas del Registro
- [ ] IBI del último ejercicio
- [ ] Recibo comunidad al corriente
- [ ] Certificado energético vigente
- [ ] Cédula de habitabilidad (si aplica)
- [ ] Licencia de primera ocupación (si obra nueva)

### Comprador
- [ ] DNI/NIE vigente
- [ ] Justificante de fondos (transferencia/cheque)
- [ ] Carta de pago/confirmación bancaria

### Inmueble
- [ ] Referencia catastral correcta
- [ ] Descripción registral coincidente
- [ ] Sin discordancias registro-catastro
- [ ] Sin cargas ocultas
- [ ] Sin ocupantes ilegales

## Validaciones Legales

### Capacidad
- [ ] Vendedor es propietario registral
- [ ] Vendedor tiene capacidad de obrar
- [ ] Si está casado, consentimiento conyugal
- [ ] Comprador mayor de edad o emancipado
- [ ] No existe prohibición de disponer

### Fiscal
- [ ] Determinado régimen fiscal (IVA vs ITP)
- [ ] Calculado importe de tributos
- [ ] Definida asunción de gastos
- [ ] Declaración valor real

### Registral
- [ ] Tracto sucesivo correcto
- [ ] Sin notas marginales problemáticas
- [ ] Superficie registral vs catastral
- [ ] Inscripción definitiva (no provisional)

[...más validaciones...]
```

## Usage in Agent Code

```python
# Agent loads the skill
await cargar_habilidad("escrituras/compraventa")

# Middleware injects SKILL.md into prompt
# Agent now has high-level knowledge

# Agent needs detailed process
proceso_detallado = backend.read("/skills/escrituras/compraventa/disclosure_proceso_detallado.md")

# Agent needs specific clauses
clausulas = backend.read("/skills/escrituras/compraventa/disclosure_clausulas.md")

# Agent checks if template exists
if backend.exists("/skills/escrituras/compraventa/resources/plantilla_base.pdf"):
    response += "\n\nPuedes encontrar la plantilla base en /skills/escrituras/compraventa/resources/plantilla_base.pdf"

# Agent verifies checklist
checklist = backend.read("/skills/escrituras/compraventa/resources/checklist.md")
# Uses checklist to validate all requirements are met
```



