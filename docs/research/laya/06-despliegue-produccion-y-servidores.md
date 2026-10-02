# Despliegue en Producción y Servidores HTTP / MCP

> Opciones de despliegue en producción: servidor HTTP de alta concurrencia `laya-serve` (compatible con el protocolo TypeSafe Jev), API FastAPI, Docker / Docker Compose, MCP Server para agentes y SDK de TypeScript / Node.js.

---

## 1. Servidor de Inferencia `laya-serve` (Protocolo TypeSafe Jev)

Laya incluye de forma nativa `laya-serve`, un demonio HTTP asíncrono construido sobre FastAPI/Uvicorn que expone exactamente el protocolo wire **`POST /v1/systemone`** de TypeSafe Jev. Cualquier cliente o SDK preexistente desarrollado para Jev puede apuntar su URL base a Laya sin modificar código.

### 1.1. Puesta en Marcha Inmediata

```bash
# Instalar con soporte de servidor
pip install "laya[serve]"

# Ejecutar en GPU con precarga de checkpoints
LAYA_DEVICE=cuda LAYA_PRELOAD=1 laya-serve
# Servidor escuchando en http://0.0.0.0:8000
```

### 1.2. Variables de Entorno de Configuración

| Variable de Entorno | Descripción | Valor por Defecto |
| :--- | :--- | :--- |
| `LAYA_HOST` | Dirección IP de enlace de red | `0.0.0.0` |
| `LAYA_PORT` | Puerto de escucha TCP | `8000` |
| `LAYA_DEVICE` | Dispositivo de inferencia (`cuda`, `mps`, `cpu`) | Detección automática |
| `LAYA_PRELOAD` | Precargar modelos al arrancar (`1`) o bajo demanda (`0`) | `1` |
| `LAYA_MODELS` | Checkpoints a mantener calientes (`english,multilingual`) | Todos |
| `LAYA_API_KEY` | Si se define, exige cabecera `Authorization: Bearer <key>` | Vacío (sin auth) |
| `LAYA_THREADS` | Límite de hilos intra-op de PyTorch en CPU | Número de núcleos físicos |
| `LAYA_MAX_TOKEN_BUDGET`| Techo máximo de tokens admitido por petición | `2048` |

### 1.3. Petición de Ejemplo (`curl`)

```bash
curl -X POST http://localhost:8000/v1/systemone \
  -H "Content-Type: application/json" \
  -d '{
    "state": {
      "document": "I was charged twice for invoice #9921. Please refund ASAP."
    },
    "questions": {
      "department": {
        "type": "choice",
        "instructions": "Which department handles this?",
        "criteria": {
          "billing": "invoices and payments",
          "technical": "system bugs and outages"
        }
      },
      "urgency": {
        "type": "score",
        "instructions": "Rate incident urgency",
        "criteria": ["low", "medium", "critical"]
      }
    }
  }'
```

---

## 2. Despliegue con Docker y Docker Compose

Para despliegues reproducibles e independientes del sistema operativo host, se recomienda montar los modelos locales mediante volúmenes para no depender de descargas durante el arranque del contenedor.

### Archivo `compose.yaml` (Producción con GPU NVIDIA)

```yaml
version: '3.8'

services:
  laya-inference:
    image: python:3.11-slim
    container_name: laya-server
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - LAYA_HOST=0.0.0.0
      - LAYA_PORT=8000
      - LAYA_DEVICE=cuda
      - LAYA_PRELOAD=1
      - LAYA_API_KEY=secret_production_key_12345
      - USE_TF=0
      - HF_HUB_OFFLINE=1
      - TRANSFORMERS_OFFLINE=1
    volumes:
      # Montar checkpoints locales descargados previamente
      - ./models:/app/models:ro
      - ./src:/app/src:ro
    working_dir: /app
    command: >
      bash -c "pip install --no-cache-dir 'laya[serve]' &&
               laya-serve"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

---

## 3. Servidor MCP para Agentes de IA (`laya-mcp-server`)

Laya incluye una implementación oficial de **Model Context Protocol (MCP)**, permitiendo que agentes de IA (Claude Code, Cursor, Antigravity, etc.) invoquen decisiones tipadas como herramientas nativas sobre `stdio`:

### 3.1. Instalación y Configuración

```bash
pip install "laya[mcp]"
```

Configuración en archivo `mcpServers` (ej. `antigravity.json` o `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "laya": {
      "command": "laya-mcp-server",
      "args": ["--device", "cuda", "--preload"],
      "env": {
        "USE_TF": "0"
      }
    }
  }
}
```

### 3.2. Herramientas Expuestas por el MCP

1. **`laya_predict`**: Ejecuta inferencia sobre un estado y conjunto de preguntas tipadas.
2. **`laya_predict_batch`**: Evalúa lotes completos de estados en paralelo.
3. **`laya_route`**: Inspecciona el motivo y modelo de enrutamiento en <0.5 ms sin computar el forward pass.
4. **`laya_decide`**: Valida estados directamente contra esquemas estructurados JSON Schema.

---

## 4. Clientes JavaScript y TypeScript

Para aplicaciones Web, microservicios Node.js o arquitecturas Edge:

### 4.1. Cliente HTTP (`laya-client`)
Consume el servidor `laya-serve` a través de HTTP:

```typescript
import { LayaClient } from 'laya-client'

const client = new LayaClient({
  baseUrl: 'http://localhost:8000',
  apiKey: 'secret_production_key_12345'
})

const result = await client.predict({
  state: { body: 'My credit card was charged twice today.' },
  questions: {
    is_fraud: {
      type: 'noul',
      instructions: 'Does the customer claim unauthorized card charges?'
    }
  }
})

console.log(result.answers.is_fraud.noul) // P(fraud)
```

### 4.2. Inferencia en el Navegador / Edge sin Python (`laya-ts`)
Si tu aplicación requiere inferencia local directa dentro de Node.js o el navegador web sin ningún backend en Python, el subproyecto `laya-ts` (`npm install laya-ts`) ejecuta los modelos convertidos a ONNX mediante `@onnxruntime/web` o `@onnxruntime/node`.
