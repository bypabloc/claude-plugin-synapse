# Descarga en Local y Modo Offline (Sin Depender de Hugging Face)

> Guía definitiva para descargar los pesos de Laya a disco local, operar en entornos totalmente desconectados de Internet (*air-gapped*), omitir autenticaciones de Hugging Face y garantizar soberanía de datos.

---

## 1. ¿Es Posible Usar Laya Sin Hugging Face?

**Sí, al 100%.** Laya fue diseñado expresamente para permitir despliegues aislados en centros de datos locales o nubes privadas.

Cuando le pasas una ruta del sistema de archivos local a `laya.load()` o a `Router(models=...)`:
- El inicializador interno ejecuta una comprobación directa con `os.path.exists(ruta)`.
- Si el directorio local existe y contiene `model.safetensors` y `rl_agent_config.json`, **el código nunca contacta la red**, no consulta la API de Hugging Face y no requiere ningún token `HF_TOKEN`.

---

## 2. Archivos Requeridos por Cada Checkpoint

Cada directorio local debe contener los siguientes artefactos obligatorios:

```
models/
├── laya/                        # Checkpoint English (~808 MB)
│   ├── model.safetensors        # Pesos ModernBERT + Decision Head
│   ├── rl_agent_config.json     # Configuración del agente y temperaturas RLCD (CRÍTICO)
│   ├── config.json              # Arquitectura base de ModernBERT
│   ├── tokenizer.json           # Tokenizador rápido
│   ├── tokenizer_config.json
│   └── special_tokens_map.json
├── laya-multilingual/           # Checkpoint Multilingual (~647 MB)
│   ├── model.safetensors        # Pesos mmBERT + Decision Head
│   ├── rl_agent_config.json     # Temperaturas y calibración
│   ├── config.json              # Arquitectura base mmBERT
│   └── ...                      # Tokenizador multilingüe (256k vocab)
└── laya-typed-decisions/        # Checkpoint Especializado (~808 MB)
    ├── model.safetensors
    ├── rl_agent_config.json
    └── ...
```

> [!CAUTION] **El Archivo `rl_agent_config.json` es Indispensable**
> Si solo descargas `model.safetensors` y `config.json` (como se haría con un modelo estándar de Hugging Face Transformers), Laya arrojará un error. Este archivo contiene la configuración de las cabezas de decisión tipadas y las tablas de temperatura calibradas.

---

## 3. Métodos para Descargar los Checkpoints a Local

### Método A: Mediante la CLI Oficial `huggingface-cli` (Recomendado)

Instala la utilidad oficial:
```bash
pip install huggingface_hub
```

Ejecuta las descargas directas a tu carpeta local de preferencia (ej. `./models/`):

```bash
# Crear directorio contenedor
mkdir -p ./models

# 1. Descargar Checkpoint English (Raíz del repo)
huggingface-cli download convaiinnovations/laya \
  --local-dir ./models/laya \
  --local-dir-use-symlinks False

# 2. Descargar Checkpoint Multilingüe
huggingface-cli download convaiinnovations/laya-multilingual \
  --local-dir ./models/laya-multilingual \
  --local-dir-use-symlinks False

# 3. Descargar Checkpoint Especializado (Opcional, para workflows de agentes)
huggingface-cli download convaiinnovations/laya-typed-decisions \
  --local-dir ./models/laya-typed-decisions \
  --local-dir-use-symlinks False
```

### Método B: Script de Automatización en Python (`snapshot_download`)

Si prefieres un script autónomo que descargue y verifique todos los pesos de una sola vez:

```python
"""Script para descarga local y verificación de checkpoints de Laya."""
import os
from huggingface_hub import snapshot_download

BASE_DIR = os.path.abspath("./models")
os.makedirs(BASE_DIR, exist_ok=True)

REPOSITORIES = {
    "laya": "convaiinnovations/laya",
    "laya-multilingual": "convaiinnovations/laya-multilingual",
    "laya-typed-decisions": "convaiinnovations/laya-typed-decisions",
}

print(f"Iniciando descarga local en: {BASE_DIR}")

for folder_name, repo_id in REPOSITORIES.items():
    target_path = os.path.join(BASE_DIR, folder_name)
    print(f"\nDescargando {repo_id} -> {target_path} ...")
    
    snapshot_download(
        repo_id=repo_id,
        local_dir=target_path,
        local_dir_use_symlinks=False,  # Descargar archivos reales, no symlinks
        resume_download=True
    )
    
    safetensors_file = os.path.join(target_path, "model.safetensors")
    config_file = os.path.join(target_path, "rl_agent_config.json")
    
    assert os.path.exists(safetensors_file), f"Falta {safetensors_file}"
    assert os.path.exists(config_file), f"Falta {config_file}"
    
    size_mb = os.path.getsize(safetensors_file) / (1024 * 1024)
    print(f"✓ {folder_name} verificado con éxito ({size_mb:.2f} MB)")

print("\nDescarga completa. Los modelos están listos para uso 100% offline.")
```

---

## 4. Cómo Cargar y Usar los Modelos Locales en Código

### 4.1. Carga Directa de un Checkpoint Individual

```python
import os
import laya

# Ruta absoluta al modelo descargado localmente
LOCAL_MODEL_PATH = os.path.abspath("./models/laya")

# Laya detecta que la ruta existe localmente y no toca Internet
agent = laya.load(LOCAL_MODEL_PATH, device="cuda")

result = agent.predict(
    "Customer requested a full refund for ticket #441",
    {"is_refund": {"type": "noul", "instructions": "Does the user ask for refund?"}}
)
print("P(refund):", result["answers"]["is_refund"]["noul"])
```

### 4.2. Configuración del `Router` para Modo Offline Total

Para que el enrutador automático utilice tus carpetas locales en lugar de intentar buscar en Hugging Face, pásale el diccionario explícito en el parámetro `models`:

```python
import os
from laya import Router

# Mapeo a las rutas locales en tu servidor
OFFLINE_MODELS = {
    "english": os.path.abspath("./models/laya"),
    "multilingual": os.path.abspath("./models/laya-multilingual"),
    "typed-decisions": os.path.abspath("./models/laya-typed-decisions"),
}

# Inicializar Router apuntando a las rutas locales
router = Router(
    models=OFFLINE_MODELS,
    preload=True,      # Precargar en memoria
    device="cuda"      # o "cpu"
)

# Inferencia multilingüe 100% offline
res_es = router.predict(
    "El pago fue duplicado en mi tarjeta de crédito.",
    {"departamento": {
        "type": "choice",
        "instructions": "Clasificar el departamento",
        "criteria": {"facturacion": "pagos y cobros", "soporte": "fallas tecnicas"}
    }}
)

print("Modelo usado:", res_es["routing"]["model"]) # multilingual
print("Resultado:", res_es["answers"]["departamento"]["choice"]) # facturacion
```

---

## 5. Blindaje de Entornos Air-Gapped (Variables de Entorno)

Para certificar ante auditorías de seguridad que el proceso no puede realizar conexiones externas bajo ninguna circunstancia, configura las siguientes variables de entorno en tu contenedor o shell:

```bash
# 1. Forzar a Hugging Face y Transformers a modo estricto offline
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

# 2. Desactivar telemetría y llamadas de red auxiliares
export HF_DATASETS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

# 3. Evitar deadlocks de TensorFlow abseil (regla obligatoria de Laya)
export USE_TF=0
```

Si ejecutas un script con estas variables activas y pasas una ruta inexistente, el sistema fallará de inmediato informando que no tiene acceso a la red, garantizando que nunca se produzcan filtraciones de datos hacia servidores externos.
