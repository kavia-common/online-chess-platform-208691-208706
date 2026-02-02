from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.api.chess_engine import ChessGame, IllegalMoveError

openapi_tags = [
    {"name": "Health", "description": "Service health and basic connectivity."},
    {"name": "Game", "description": "Chess game state, moves, history, and restart."},
]


app = FastAPI(
    title="Retro Chess Backend",
    description=(
        "In-memory chess engine + REST API used by the React frontend.\n\n"
        "Contract notes:\n"
        "- Board is a dict mapping squares to piece letters: `{\"e2\": \"P\", \"e7\": \"p\"}`\n"
        "- `turn` is `\"white\"` or `\"black\"`\n"
        "- History is returned as `{ moves: [...] }` and is also included inline in `/move` response\n"
    ),
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# In-memory singleton (single active game as requested)
_GAME = ChessGame()

# Note: In production you should restrict allow_origins to your deployed frontend URL(s).
# We include common local/dev origins plus "*" fallback to keep Kavia preview/simple dev working.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GameStateResponse(BaseModel):
    board: dict = Field(..., description="Board map: square => piece char (uppercase=white, lowercase=black).")
    turn: Literal["white", "black"] = Field(..., description="Side to move.")
    move_count: int = Field(..., description="Number of half-moves recorded so far.")


class MoveHistoryItem(BaseModel):
    from_: str = Field(..., alias="from", description="Origin square, e.g. 'e2'.")
    to: str = Field(..., description="Destination square, e.g. 'e4'.")
    promotion: Optional[str] = Field(None, description="Promotion piece (Q/R/B/N) if applicable.")
    san: Optional[str] = Field(None, description="A minimal SAN-like string for display/debugging.")


class MoveHistoryResponse(BaseModel):
    moves: list[MoveHistoryItem] = Field(..., description="List of moves made so far.")


class MoveRequest(BaseModel):
    from_: str = Field(..., alias="from", description="Origin square like 'e2'.")
    to: str = Field(..., description="Destination square like 'e4'.")
    promotion: Optional[str] = Field(
        None,
        description="Optional promotion piece for pawn promotions: one of Q/R/B/N. If omitted, defaults to Q.",
    )


class MoveResponse(BaseModel):
    board: dict = Field(..., description="Updated board map.")
    turn: Literal["white", "black"] = Field(..., description="Updated side to move.")
    move_count: int = Field(..., description="Updated half-move count.")
    moves: list[MoveHistoryItem] = Field(..., description="Move history including the newly applied move.")


@app.get("/", tags=["Health"], summary="Health check", operation_id="health_check")
# PUBLIC_INTERFACE
def health_check():
    """Return a basic health payload for frontend connectivity checks."""
    return {"message": "Healthy"}


@app.get("/game", response_model=GameStateResponse, tags=["Game"], summary="Get current game state", operation_id="get_game")
# PUBLIC_INTERFACE
def get_game_state():
    """Get the current in-memory chess game state (board + side to move)."""
    return _GAME.get_state()


@app.get(
    "/history",
    response_model=MoveHistoryResponse,
    tags=["Game"],
    summary="Get move history",
    operation_id="get_history",
)
# PUBLIC_INTERFACE
def get_move_history():
    """Get the list of moves made so far."""
    return _GAME.get_history()


@app.post(
    "/move",
    response_model=MoveResponse,
    tags=["Game"],
    summary="Submit a move",
    operation_id="submit_move",
)
# PUBLIC_INTERFACE
def submit_move(req: MoveRequest):
    """
    Submit a move and receive updated game state.

    - Validates piece movement rules
    - Validates that the moving side is correct
    - Validates king safety (move cannot leave your king in check)
    - Supports castling, en-passant, and promotion (defaults to Q)

    Returns:
        Combined payload { board, turn, move_count, moves } as expected by the React UI.
    """
    try:
        return _GAME.apply_move(from_sq=req.from_, to_sq=req.to, promotion=req.promotion)
    except IllegalMoveError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        # Covers square parsing errors, etc.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # Avoid leaking internals; keep a deterministic contract.
        raise HTTPException(status_code=500, detail="Internal server error.") from e


@app.post(
    "/restart",
    response_model=GameStateResponse,
    tags=["Game"],
    summary="Restart the game",
    operation_id="restart_game",
)
# PUBLIC_INTERFACE
def restart_game():
    """
    Reset the in-memory game to the standard chess starting position.

    Returns:
        Fresh game state { board, turn, move_count }.
    """
    _GAME.reset()
    return _GAME.get_state()
