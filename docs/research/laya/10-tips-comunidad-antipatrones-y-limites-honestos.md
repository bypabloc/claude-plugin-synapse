# Tips de la Comunidad, Antipatrones y Límites Honestos

> Recopilación técnica de problemas documentados, trampas frecuentes detectadas por la comunidad en producción, soluciones verificadas y límites reales de la arquitectura de Laya.

---

## 1. La Trampa de la Primitiva `noul` (GitHub Issue #156)

### El Problema
La primitiva `noul` está pensada para responder Sí/No. Para lograrlo, el compilador interno de Laya renderiza las dos opciones como las cadenas textuales `false:` y `true:`.

En el checkpoint en inglés (`convaiinnovations/laya`), se descubrió que este par de etiquetas puede dominar la atención de la red por encima del contenido del estado, sesgando el modelo hacia un `"no"` rotundo y sobreconfiado, incluso ante textos evidentemente afirmativos.

### La Solución de la Comunidad
Si observas que `noul` se muestra sesgado o insensible ante variaciones en tus datos, **reformula la pregunta como un `choice` de 2 opciones con claves neutras ("A" y "B")**:

```python
# ANTIPATRÓN (Riesgo de sesgo en el checkpoint en inglés)
{"type": "noul", "instructions": "Is this customer review positive?"}

# PATRÓN CORRECTO (Robusto y libre de sesgo léxico)
{
    "type": "choice",
    "instructions": "Determine if the customer review is positive or negative",
    "criteria": {
        "A": "yes, the review expresses satisfaction or praise",
        "B": "no, the review expresses disappointment, anger or complaints"
    }
}
```

---

## 2. La Falacia de `action.act_probability` (GitHub Issue #185)

### El Problema
Los resultados de Laya incluyen un campo llamado `action.act_probability`, pensado originalmente para que el modelo decidiera de forma autónoma si debía ejecutar una acción directa o escalar a un humano.

Sin embargo, en mediciones independientes sobre cientos de decisiones etiquetadas:
- `action.act_probability` arrojó valores cercanos a `1.0` en casi el 100% de las entradas.
- Sus logits brutos correlacionaron negativamente con la exactitud real (un paupérrimo **AUROC de 0.30**).

### La Solución de la Comunidad
**Ignorar por completo `action.act_probability`**. Para cualquier política de escalamiento a humanos, realiza el gating sobre **`answer_confidence`**, el cual alcanza un **AUROC de 0.77** en los mismos datos:

```python
# ANTIPATRÓN
if result["action"]["act_probability"] > 0.8:
    execute_action()

# PATRÓN CORRECTO
best_answer = result["answers"]["my_question"]
if best_answer["answer_confidence"] >= 0.85:
    execute_action()
else:
    escalate_to_human_operator()
```

---

## 3. Asignación de Presupuesto en Preguntas de Alta Cardinalidad

### El Problema
Las secuencias de Laya dividen los tokens entre las opciones (`head_max_len`) y el documento (`max_len - head_max_len`). Cuando formulas una pregunta con 50 o más opciones (ej. 77 categorías bancarias):

$$\text{tokens\_por\_opción} = \max\left(4, \left\lfloor\frac{192 - 16}{77}\right\rfloor\right) = 4 \text{ tokens}$$

Cada opción recibe únicamente el token `[MASK]` y 3 subpalabras. Las descripciones se truncan de forma invisible y la exactitud colapsa drásticamente (de 0.87 a 0.42).

### Las Soluciones de la Comunidad
1. **Ampliar el presupuesto de cabecera**:
   ```python
   agent.cfg["head_max_len"] = 512
   agent.cfg["max_len"] = 1024
   ```
2. **Clasificación Jerárquica en 2 Fases (Coarse-to-Fine)**: Agrupar las 77 categorías en 5 dominios macro (Paso 1) y luego clasificar la categoría final dentro del dominio seleccionado (Paso 2). Esto mantiene cada paso con menos de 15 opciones y latencia total de solo ~65 ms.

---

## 4. El Deadlock de TensorFlow Abseil (`USE_TF=0`)

### El Problema
Al importar la biblioteca `transformers`, esta sondea si TensorFlow está presente en el entorno de Python. Si TensorFlow está instalado (algo estándar en entornos como Google Colab, Kaggle o imágenes Docker de ML generales), la inicialización del runtime de Abseil en C++ puede provocar un **bloqueo mutuo irreversible (deadlock)** al construir los grafos de PyTorch.

### La Solución
Configurar obligatoriamente la variable de entorno `USE_TF=0` **antes de importar cualquier módulo de PyTorch o Transformers**:

```python
import os
os.environ["USE_TF"] = "0"
os.environ["USE_TORCH"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import laya
```

---

## 5. Sobreconfianza de Fábrica y Calibración por Dominio

### El Problema
Aunque Laya se entrena con reglas de puntuación estrictamente propias (RLCD), los checkpoints distribuidos sin calibrar tienden a exhibir sobreconfianza en dominios no vistos:
- Checkpoint English: Error de Calibración Esperado (ECE) promedio de **0.466**.
- Checkpoint Multilingual: ECE promedio de **0.314**.

### La Solución
Ajustar las temperaturas de escalado (*temperature scaling*) sobre un lote pequeño de validación (100–200 ejemplos propios). Al reajustar una temperatura por tupla `(tipo_pregunta, num_opciones)`, el ECE cae a **0.081** (inglés) y **0.106** (multilingüe), logrando que un 0.85 signifique verdaderamente un 85% de probabilidad real.

---

## 6. Laya no es un Oráculo Zero-Shot Universal

### Expectativa vs Realidad
- **Expectativa errónea**: Creer que Laya reemplaza a Claude 3.7 o GPT-4 para razonamiento complejo zero-shot sin entrenamiento previo. En el benchmark `typed-decisions`, los modelos base sin ajustar obtienen **0.362** de exactitud (apenas por encima del azar de 0.318).
- **Realidad comprobada**: Laya es un **esqueleto de decisión System 1 para especializar**. Tras realizar fine-tuning con RLCD en 1,000 ejemplos del dominio objetivo, la exactitud sube a **0.766** (superando a TypeSafe Jev comercial y al techo de concordancia humana).

---

## 7. Optimización de Hilos en CPU (`LAYA_THREADS`)

### El Problema
En servidores multinúcleo basados en CPU (Intel Xeon / AMD EPYC), dejar que PyTorch asigne hilos equivalentes a todos los núcleos lógicos (Hyper-Threading / SMT) degrada el rendimiento en más de un 40% debido a la contención en la caché L3 y la sobrecarga de sincronización de OpenMP.

### La Solución
Limitar siempre los hilos al número de **núcleos físicos**:

```bash
# Para una CPU de 8 núcleos físicos (16 hilos)
export LAYA_THREADS=8
```
o en Python:
```python
import torch
torch.set_num_threads(8)
```
