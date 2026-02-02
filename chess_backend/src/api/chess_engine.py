from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

Color = Literal["white", "black"]
PieceType = Literal["P", "N", "B", "R", "Q", "K"]
Square = str  # e.g. "e2"
BoardMap = Dict[Square, str]  # frontend expects "e2": "P" (white), "e7": "p" (black)


FILES = "abcdefgh"
RANKS = "12345678"

KNIGHT_DELTAS = [
    (1, 2),
    (2, 1),
    (2, -1),
    (1, -2),
    (-1, -2),
    (-2, -1),
    (-2, 1),
    (-1, 2),
]
KING_DELTAS = [
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
]


def _in_bounds(f: int, r: int) -> bool:
    return 0 <= f < 8 and 0 <= r < 8


def _sq_to_coords(sq: Square) -> Tuple[int, int]:
    if len(sq) != 2 or sq[0] not in FILES or sq[1] not in RANKS:
        raise ValueError(f"Invalid square: {sq}")
    return FILES.index(sq[0]), RANKS.index(sq[1])


def _coords_to_sq(f: int, r: int) -> Square:
    return f"{FILES[f]}{RANKS[r]}"


def _piece_color(piece: str) -> Color:
    return "white" if piece.isupper() else "black"


def _piece_type(piece: str) -> PieceType:
    return piece.upper()  # type: ignore[return-value]


def _opponent(color: Color) -> Color:
    return "black" if color == "white" else "white"


@dataclass(frozen=True)
class Move:
    """Internal move representation."""

    from_sq: Square
    to_sq: Square
    promotion: Optional[PieceType] = None
    is_en_passant: bool = False
    is_castle_kingside: bool = False
    is_castle_queenside: bool = False


class IllegalMoveError(ValueError):
    """Raised when a move is syntactically valid but illegal in the current position."""


class ChessGame:
    """
    In-memory chess game state with legal move validation (including king safety).

    Scope/assumptions:
    - Single active game (handled by API module as a global singleton).
    - Supports: normal moves, captures, pawn double, en-passant, castling, promotion.
    - Provides: board map (dict of squares to piece chars), turn, move history.

    Note:
    - This engine is intentionally lightweight (no external deps).
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset to starting position."""
        self.board: BoardMap = {}
        # White pieces
        self._set_back_rank("white")
        for f in FILES:
            self.board[f"{f}2"] = "P"
        # Black pieces
        self._set_back_rank("black")
        for f in FILES:
            self.board[f"{f}7"] = "p"

        self.turn: Color = "white"
        self.move_number: int = 1  # increments after black move
        self.halfmove_clock: int = 0

        # Castling rights
        self.white_can_castle_kingside = True
        self.white_can_castle_queenside = True
        self.black_can_castle_kingside = True
        self.black_can_castle_queenside = True

        # En passant target square (square *behind* the pawn that advanced two)
        self.en_passant_target: Optional[Square] = None

        # History as frontend-friendly dicts
        self.history: List[dict] = []

    def _set_back_rank(self, color: Color) -> None:
        rank = "1" if color == "white" else "8"
        pieces = ["R", "N", "B", "Q", "K", "B", "N", "R"]
        for i, f in enumerate(FILES):
            p = pieces[i]
            self.board[f"{f}{rank}"] = p if color == "white" else p.lower()

    def get_state(self) -> dict:
        """Return frontend-friendly state JSON."""
        return {
            "board": dict(self.board),
            "turn": self.turn,
            "move_count": len(self.history),
        }

    def get_history(self) -> dict:
        """Return frontend-friendly move history JSON."""
        return {"moves": list(self.history)}

    def apply_move(self, from_sq: Square, to_sq: Square, promotion: Optional[str] = None) -> dict:
        """
        Apply a move if legal, otherwise raise IllegalMoveError.

        Returns a combined payload that frontend can consume:
        { board, turn, moves }
        """
        from_sq = from_sq.strip().lower()
        to_sq = to_sq.strip().lower()
        promo_pt: Optional[PieceType] = None
        if promotion:
            p = promotion.strip().upper()
            if p not in {"Q", "R", "B", "N"}:
                raise IllegalMoveError("Invalid promotion piece. Use one of: Q, R, B, N.")
            promo_pt = p  # type: ignore[assignment]

        move = self._validate_and_build_move(from_sq, to_sq, promo_pt)
        self._make_move(move)

        payload = self.get_state()
        payload.update(self.get_history())
        return payload

    def _validate_and_build_move(self, from_sq: Square, to_sq: Square, promotion: Optional[PieceType]) -> Move:
        if from_sq == to_sq:
            raise IllegalMoveError("from and to squares must be different.")
        _sq_to_coords(from_sq)
        _sq_to_coords(to_sq)

        piece = self.board.get(from_sq)
        if not piece:
            raise IllegalMoveError(f"No piece on {from_sq}.")
        if _piece_color(piece) != self.turn:
            raise IllegalMoveError("It's not that piece's turn to move.")

        pseudo = self._generate_pseudo_legal_moves_for_piece(from_sq, piece)
        candidate = next((m for m in pseudo if m.to_sq == to_sq), None)
        if not candidate:
            raise IllegalMoveError("Move is not legal for that piece.")

        # Handle promotion requirement
        if _piece_type(piece) == "P":
            _, r_to = _sq_to_coords(to_sq)
            if (self.turn == "white" and r_to == 7) or (self.turn == "black" and r_to == 0):
                if promotion is None:
                    # frontend currently doesn't send promotion; default to queen to keep UX simple
                    promotion = "Q"
                candidate = Move(
                    from_sq=candidate.from_sq,
                    to_sq=candidate.to_sq,
                    promotion=promotion,
                    is_en_passant=candidate.is_en_passant,
                    is_castle_kingside=candidate.is_castle_kingside,
                    is_castle_queenside=candidate.is_castle_queenside,
                )

        # Check legality: king must not be left in check
        if not self._is_legal_after_move(candidate):
            raise IllegalMoveError("Illegal move: king would be in check.")

        return candidate

    def _is_legal_after_move(self, move: Move) -> bool:
        snapshot = self._snapshot()
        try:
            self._make_move(move, record_history=False)
            return not self._is_in_check(_opponent(self.turn))  # after _make_move, turn already toggled
        finally:
            self._restore(snapshot)

    def _snapshot(self) -> dict:
        return {
            "board": dict(self.board),
            "turn": self.turn,
            "move_number": self.move_number,
            "halfmove_clock": self.halfmove_clock,
            "wck": self.white_can_castle_kingside,
            "wcq": self.white_can_castle_queenside,
            "bck": self.black_can_castle_kingside,
            "bcq": self.black_can_castle_queenside,
            "ep": self.en_passant_target,
            "history": list(self.history),
        }

    def _restore(self, snap: dict) -> None:
        self.board = dict(snap["board"])
        self.turn = snap["turn"]
        self.move_number = snap["move_number"]
        self.halfmove_clock = snap["halfmove_clock"]
        self.white_can_castle_kingside = snap["wck"]
        self.white_can_castle_queenside = snap["wcq"]
        self.black_can_castle_kingside = snap["bck"]
        self.black_can_castle_queenside = snap["bcq"]
        self.en_passant_target = snap["ep"]
        self.history = list(snap["history"])

    def _make_move(self, move: Move, record_history: bool = True) -> None:
        piece = self.board.get(move.from_sq)
        if not piece:
            raise IllegalMoveError("Internal error: moving missing piece.")

        captured_piece: Optional[str] = None

        # Update halfmove clock
        is_pawn = _piece_type(piece) == "P"
        is_capture = move.to_sq in self.board or move.is_en_passant

        # Clear en-passant target by default; set again if pawn double advances
        prev_ep = self.en_passant_target
        self.en_passant_target = None

        # Castling
        if move.is_castle_kingside or move.is_castle_queenside:
            if _piece_type(piece) != "K":
                raise IllegalMoveError("Internal error: non-king castling move.")
            if self._is_in_check(self.turn):
                raise IllegalMoveError("Cannot castle out of check.")

            if self.turn == "white":
                king_from, king_to = "e1", ("g1" if move.is_castle_kingside else "c1")
                rook_from, rook_to = ("h1", "f1") if move.is_castle_kingside else ("a1", "d1")
                self.white_can_castle_kingside = False
                self.white_can_castle_queenside = False
            else:
                king_from, king_to = "e8", ("g8" if move.is_castle_kingside else "c8")
                rook_from, rook_to = ("h8", "f8") if move.is_castle_kingside else ("a8", "d8")
                self.black_can_castle_kingside = False
                self.black_can_castle_queenside = False

            # Squares between must be empty and not attacked
            path = ["f1", "g1"] if self.turn == "white" and move.is_castle_kingside else []
            if self.turn == "white" and move.is_castle_queenside:
                path = ["d1", "c1"]
            if self.turn == "black" and move.is_castle_kingside:
                path = ["f8", "g8"]
            if self.turn == "black" and move.is_castle_queenside:
                path = ["d8", "c8"]

            between_empty = True
            if move.is_castle_kingside:
                between = ["f1", "g1"] if self.turn == "white" else ["f8", "g8"]
            else:
                between = ["b1", "c1", "d1"] if self.turn == "white" else ["b8", "c8", "d8"]
            for sq in between:
                if sq in self.board:
                    between_empty = False
                    break
            if not between_empty:
                raise IllegalMoveError("Cannot castle through pieces.")

            # King may not pass through attacked squares
            for sq in [king_from] + path:
                if self._is_square_attacked(sq, _opponent(self.turn)):
                    raise IllegalMoveError("Cannot castle through check.")

            # Move pieces
            self.board.pop(king_from, None)
            self.board.pop(rook_from, None)
            self.board[king_to] = piece
            self.board[rook_to] = "R" if self.turn == "white" else "r"

            self._finalize_turn(is_pawn=False, is_capture=False)

            if record_history:
                self.history.append(
                    {
                        "from": move.from_sq,
                        "to": move.to_sq,
                        "promotion": move.promotion,
                        "san": "O-O" if move.is_castle_kingside else "O-O-O",
                    }
                )
            return

        # En passant capture
        if move.is_en_passant:
            # Captured pawn is behind the target square
            f_to, r_to = _sq_to_coords(move.to_sq)
            cap_r = r_to - 1 if self.turn == "white" else r_to + 1
            cap_sq = _coords_to_sq(f_to, cap_r)
            captured_piece = self.board.pop(cap_sq, None)
            if not captured_piece or _piece_type(captured_piece) != "P":
                raise IllegalMoveError("Invalid en passant capture.")

        # Normal capture
        if move.to_sq in self.board:
            captured_piece = self.board.pop(move.to_sq)

        # Move piece
        self.board.pop(move.from_sq, None)
        moved_piece = piece

        # Promotion
        if move.promotion:
            moved_piece = move.promotion if self.turn == "white" else move.promotion.lower()

        self.board[move.to_sq] = moved_piece

        # Update castling rights if king/rook moved or rook captured
        self._update_castling_rights_after_move(piece, move.from_sq, move.to_sq, captured_piece)

        # Set en passant target if pawn double moved
        if _piece_type(piece) == "P":
            f_from, r_from = _sq_to_coords(move.from_sq)
            _, r_to = _sq_to_coords(move.to_sq)
            if abs(r_to - r_from) == 2:
                ep_r = (r_from + r_to) // 2
                self.en_passant_target = _coords_to_sq(f_from, ep_r)

        self._finalize_turn(is_pawn=is_pawn, is_capture=is_capture)

        if record_history:
            self.history.append(
                {
                    "from": move.from_sq,
                    "to": move.to_sq,
                    "promotion": move.promotion,
                    # Keep SAN minimal; frontend only needs from/to today.
                    "san": self._simple_san(piece, move, captured_piece, prev_ep),
                }
            )

    def _finalize_turn(self, is_pawn: bool, is_capture: bool) -> None:
        if is_pawn or is_capture:
            self.halfmove_clock = 0
        else:
            self.halfmove_clock += 1

        if self.turn == "black":
            self.move_number += 1
        self.turn = _opponent(self.turn)

    def _update_castling_rights_after_move(
        self, moved_piece: str, from_sq: Square, to_sq: Square, captured_piece: Optional[str]
    ) -> None:
        pt = _piece_type(moved_piece)
        mover = _piece_color(moved_piece)

        if pt == "K":
            if mover == "white":
                self.white_can_castle_kingside = False
                self.white_can_castle_queenside = False
            else:
                self.black_can_castle_kingside = False
                self.black_can_castle_queenside = False

        if pt == "R":
            if from_sq == "h1":
                self.white_can_castle_kingside = False
            if from_sq == "a1":
                self.white_can_castle_queenside = False
            if from_sq == "h8":
                self.black_can_castle_kingside = False
            if from_sq == "a8":
                self.black_can_castle_queenside = False

        # If a rook is captured from its home square, remove corresponding right
        if captured_piece and _piece_type(captured_piece) == "R":
            if to_sq == "h1":
                self.white_can_castle_kingside = False
            if to_sq == "a1":
                self.white_can_castle_queenside = False
            if to_sq == "h8":
                self.black_can_castle_kingside = False
            if to_sq == "a8":
                self.black_can_castle_queenside = False

    def _simple_san(
        self,
        moved_piece: str,
        move: Move,
        captured_piece: Optional[str],
        prev_ep: Optional[Square],
    ) -> str:
        pt = _piece_type(moved_piece)
        if move.is_castle_kingside:
            return "O-O"
        if move.is_castle_queenside:
            return "O-O-O"

        capture = "x" if captured_piece else ""
        promo = f"={move.promotion}" if move.promotion else ""
        piece_letter = "" if pt == "P" else pt

        # Pawn capture includes from-file (e.g., exd5). Keep minimal but helpful.
        if pt == "P" and capture:
            return f"{move.from_sq[0]}x{move.to_sq}{promo}"
        if pt == "P":
            return f"{move.to_sq}{promo}"
        return f"{piece_letter}{capture}{move.to_sq}{promo}"

    def _find_king(self, color: Color) -> Optional[Square]:
        king = "K" if color == "white" else "k"
        for sq, p in self.board.items():
            if p == king:
                return sq
        return None

    def _is_in_check(self, color: Color) -> bool:
        king_sq = self._find_king(color)
        if not king_sq:
            # Should never happen in normal play; treat as check to block illegal states.
            return True
        return self._is_square_attacked(king_sq, _opponent(color))

    def _is_square_attacked(self, square: Square, by_color: Color) -> bool:
        # For each enemy piece, see if it attacks `square`.
        for sq, piece in self.board.items():
            if _piece_color(piece) != by_color:
                continue
            if self._piece_attacks_square(sq, piece, square):
                return True
        return False

    def _piece_attacks_square(self, from_sq: Square, piece: str, target_sq: Square) -> bool:
        pt = _piece_type(piece)
        f_from, r_from = _sq_to_coords(from_sq)
        f_t, r_t = _sq_to_coords(target_sq)
        df = f_t - f_from
        dr = r_t - r_from

        if pt == "P":
            # Pawns attack diagonally forward (from their perspective)
            direction = 1 if _piece_color(piece) == "white" else -1
            return dr == direction and abs(df) == 1

        if pt == "N":
            return (df, dr) in KNIGHT_DELTAS

        if pt == "K":
            return max(abs(df), abs(dr)) == 1

        if pt in {"B", "R", "Q"}:
            # Sliding piece: check direction matches and path is clear
            if pt == "B" and abs(df) != abs(dr):
                return False
            if pt == "R" and not (df == 0 or dr == 0):
                return False
            if pt == "Q" and not (df == 0 or dr == 0 or abs(df) == abs(dr)):
                return False

            step_f = 0 if df == 0 else (1 if df > 0 else -1)
            step_r = 0 if dr == 0 else (1 if dr > 0 else -1)
            cur_f, cur_r = f_from + step_f, r_from + step_r
            while (cur_f, cur_r) != (f_t, r_t):
                if not _in_bounds(cur_f, cur_r):
                    return False
                if _coords_to_sq(cur_f, cur_r) in self.board:
                    return False
                cur_f += step_f
                cur_r += step_r
            return True

        return False

    def _generate_pseudo_legal_moves_for_piece(self, from_sq: Square, piece: str) -> List[Move]:
        pt = _piece_type(piece)
        if pt == "P":
            return self._pawn_moves(from_sq, piece)
        if pt == "N":
            return self._knight_moves(from_sq, piece)
        if pt == "B":
            return self._slider_moves(from_sq, piece, deltas=[(1, 1), (1, -1), (-1, 1), (-1, -1)])
        if pt == "R":
            return self._slider_moves(from_sq, piece, deltas=[(1, 0), (-1, 0), (0, 1), (0, -1)])
        if pt == "Q":
            return self._slider_moves(
                from_sq,
                piece,
                deltas=[(1, 1), (1, -1), (-1, 1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)],
            )
        if pt == "K":
            return self._king_moves(from_sq, piece)
        return []

    def _pawn_moves(self, from_sq: Square, piece: str) -> List[Move]:
        moves: List[Move] = []
        color = _piece_color(piece)
        f, r = _sq_to_coords(from_sq)
        direction = 1 if color == "white" else -1
        start_rank = 1 if color == "white" else 6  # 0-based rank index (rank "2"/"7")
        one_step_r = r + direction

        # Forward one
        if _in_bounds(f, one_step_r):
            one_sq = _coords_to_sq(f, one_step_r)
            if one_sq not in self.board:
                moves.append(Move(from_sq, one_sq))
                # Forward two from start
                if r == start_rank:
                    two_step_r = r + 2 * direction
                    two_sq = _coords_to_sq(f, two_step_r)
                    if _in_bounds(f, two_step_r) and two_sq not in self.board:
                        moves.append(Move(from_sq, two_sq))

        # Captures
        for df in (-1, 1):
            cf = f + df
            cr = r + direction
            if not _in_bounds(cf, cr):
                continue
            cap_sq = _coords_to_sq(cf, cr)
            if cap_sq in self.board and _piece_color(self.board[cap_sq]) != color:
                moves.append(Move(from_sq, cap_sq))

        # En passant
        if self.en_passant_target:
            try:
                ef, er = _sq_to_coords(self.en_passant_target)
            except ValueError:
                ef, er = (-1, -1)
            if er == r + direction and abs(ef - f) == 1:
                moves.append(Move(from_sq, self.en_passant_target, is_en_passant=True))

        return moves

    def _knight_moves(self, from_sq: Square, piece: str) -> List[Move]:
        moves: List[Move] = []
        color = _piece_color(piece)
        f, r = _sq_to_coords(from_sq)
        for df, dr in KNIGHT_DELTAS:
            nf, nr = f + df, r + dr
            if not _in_bounds(nf, nr):
                continue
            to_sq = _coords_to_sq(nf, nr)
            if to_sq not in self.board or _piece_color(self.board[to_sq]) != color:
                moves.append(Move(from_sq, to_sq))
        return moves

    def _slider_moves(self, from_sq: Square, piece: str, deltas: List[Tuple[int, int]]) -> List[Move]:
        moves: List[Move] = []
        color = _piece_color(piece)
        f, r = _sq_to_coords(from_sq)
        for df, dr in deltas:
            nf, nr = f + df, r + dr
            while _in_bounds(nf, nr):
                to_sq = _coords_to_sq(nf, nr)
                if to_sq in self.board:
                    if _piece_color(self.board[to_sq]) != color:
                        moves.append(Move(from_sq, to_sq))
                    break
                moves.append(Move(from_sq, to_sq))
                nf += df
                nr += dr
        return moves

    def _king_moves(self, from_sq: Square, piece: str) -> List[Move]:
        moves: List[Move] = []
        color = _piece_color(piece)
        f, r = _sq_to_coords(from_sq)
        for df, dr in KING_DELTAS:
            nf, nr = f + df, r + dr
            if not _in_bounds(nf, nr):
                continue
            to_sq = _coords_to_sq(nf, nr)
            if to_sq not in self.board or _piece_color(self.board[to_sq]) != color:
                moves.append(Move(from_sq, to_sq))

        # Castling pseudo-moves (full legality checked during make/validation)
        if color == "white" and from_sq == "e1":
            if self.white_can_castle_kingside and "f1" not in self.board and "g1" not in self.board:
                moves.append(Move(from_sq, "g1", is_castle_kingside=True))
            if (
                self.white_can_castle_queenside
                and "d1" not in self.board
                and "c1" not in self.board
                and "b1" not in self.board
            ):
                moves.append(Move(from_sq, "c1", is_castle_queenside=True))
        if color == "black" and from_sq == "e8":
            if self.black_can_castle_kingside and "f8" not in self.board and "g8" not in self.board:
                moves.append(Move(from_sq, "g8", is_castle_kingside=True))
            if (
                self.black_can_castle_queenside
                and "d8" not in self.board
                and "c8" not in self.board
                and "b8" not in self.board
            ):
                moves.append(Move(from_sq, "c8", is_castle_queenside=True))

        return moves

    def list_legal_moves(self) -> List[dict]:
        """
        List all legal moves for the side to move.

        Not currently used by the frontend but useful for debugging / future UI.
        """
        res: List[dict] = []
        for sq, piece in self.board.items():
            if _piece_color(piece) != self.turn:
                continue
            for mv in self._generate_pseudo_legal_moves_for_piece(sq, piece):
                if self._is_legal_after_move(mv):
                    res.append({"from": mv.from_sq, "to": mv.to_sq, "promotion": mv.promotion})
        return res
