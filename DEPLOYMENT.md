# Quick start: Local Deployment and Testing

### Quick Start with Make

1. **Clone and Setup:**
   ```bash
   git clone https://github.com/Vexa-ai/vexa
   cd vexa
   ```

2. **Configure OpenAI API Key:**
   ```bash
   cp env-example .env
   # Edit .env and add your OpenAI API key:
   # OPENAI_API_KEY=sk-your-actual-key-here
   ```

3. **Start the System:**
   ```bash
   make all
   ```

This will:
- Create environment configuration
- Build all Docker images
- Start all services
- Initialize the database

### API Documentation

API docs are available at:
- Main API: http://localhost:8056/docs
- Admin API: http://localhost:8057/docs

### Managing Services

- `make ps`: Show container status
- `make logs`: Tail logs
- `make down`: Stop all services
- `make test`: Run interaction test