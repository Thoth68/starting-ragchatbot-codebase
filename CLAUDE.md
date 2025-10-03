# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Course Materials RAG System** - A FastAPI-based retrieval-augmented generation system for querying course materials using ChromaDB vector storage and Claude AI.

**Tech Stack**: FastAPI, ChromaDB, Anthropic Claude, Sentence Transformers
**Language**: Python 3.13+
**Package Manager**: uv

## Setup and Development Commands

### Initial Setup
```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env and add ANTHROPIC_API_KEY=your-key-here
```

### Running the Application
```bash
# Start development server (from project root)
./run.sh

# Or manually
cd backend && uv run uvicorn app:app --reload --port 8000
```

### Testing the API
```bash
# Reset database (clear all data)
curl -X DELETE http://localhost:8000/api/reset

# Get course list
curl http://localhost:8000/api/courses

# Query the system
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is RAG?"}'
```

**Access Points:**
- Web UI: `http://localhost:8000`
- API Docs: `http://localhost:8000/docs`

## Architecture Overview

### System Flow

**Document Ingestion:**
1. Documents in `docs/*.txt` are auto-loaded on startup (`app.py:88`)
2. `DocumentProcessor` parses metadata and splits into lessons
3. Text chunked (800 chars, 100 overlap) with sentence-aware splitting
4. Chunks embedded using `all-MiniLM-L6-v2` and stored in ChromaDB

**Query Processing:**
1. Query → `RAGSystem.query()` (rag_system.py:102)
2. Tool-based search via `CourseSearchTool` (search_tools.py)
3. Vector search retrieves top 5 relevant chunks
4. Claude API generates streaming response with context
5. Response saved to session history (last 2 messages)

### Core Components

**RAGSystem** (`rag_system.py`) - Main orchestrator
- Coordinates DocumentProcessor, VectorStore, AIGenerator, SessionManager
- Entry point: `query()` method for user queries

**VectorStore** (`vector_store.py`) - ChromaDB wrapper
- Two collections: `course_catalog` (metadata) and `course_content` (chunks)
- Handles embedding and similarity search

**AIGenerator** (`ai_generator.py`) - Claude API client
- Model: `claude-sonnet-4-20250514`
- Supports streaming responses and tool use

**SessionManager** (`session_manager.py`) - In-memory conversation state
- Stores last 2 messages per session (configurable via `MAX_HISTORY`)

### Configuration (backend/config.py)

Key settings in `Config` dataclass:
- `CHUNK_SIZE: 800` - Text chunk size for embeddings
- `CHUNK_OVERLAP: 100` - Overlap between chunks
- `MAX_RESULTS: 5` - Number of search results
- `MAX_HISTORY: 2` - Conversation messages to retain
- `CHROMA_PATH: "./chroma_db"` - Vector database location

**Environment Variables (.env):**
- `ANTHROPIC_API_KEY` - Required for Claude API access

### API Endpoints (backend/app.py)

- `POST /api/query` - Process user query with RAG
- `GET /api/courses` - Get course statistics
- Startup event auto-loads documents from `../docs`

### Data Models (backend/models.py)

- `Course` - Course metadata (title, instructor, lessons)
- `Lesson` - Individual lesson content
- `CourseChunk` - Embedded text chunk with metadata

## Key Implementation Details

**Chunking Algorithm** (document_processor.py):
- Normalizes whitespace
- Splits on sentence boundaries (regex: `[.!?]+\s+`)
- Groups sentences up to `CHUNK_SIZE`
- Adds `CHUNK_OVERLAP` from previous chunk
- Prepends context: `"Course {title} Lesson {num} content: ..."`

**Tool-Based Search** (search_tools.py):
- Claude can call `search_course_materials` tool during response generation
- Supports filtering by course title and/or lesson number
- Returns formatted sources with metadata

**ChromaDB Collections**:
- `course_catalog`: Stores course-level metadata for semantic course search
- `course_content`: Stores chunked text with embeddings for content search

## Important Development Notes

### Package Manager
**CRITICAL: Always use `uv` for ALL dependency management and Python execution - NEVER use `pip` or `python` directly**

**Dependency Management:**
- Install all dependencies: `uv sync`
- Add new packages: `uv add <package>`
- Remove packages: `uv remove <package>`
- Update dependencies: `uv lock --upgrade`

**Running Python:**
- Run Python files: `uv run python script.py`
- Run server: `uv run uvicorn app:app --reload --port 8000`
- Run any Python command: `uv run <command>`

**DO NOT use:**
- `pip install` / `pip uninstall`
- `python -m pip`
- `python script.py` (use `uv run python script.py` instead)
- Direct `python` commands without `uv run` prefix

### Windows Development
Use **Git Bash** to run shell scripts on Windows (not PowerShell/CMD).
