# history.py
"""Менеджер отмены/повтора действий (стек снимков состояния модели)."""
from collections import deque
from config import HISTORY_LIMIT


class HistoryManager:
    def __init__(self, limit=HISTORY_LIMIT):
        self._undo = deque(maxlen=limit)
        self._redo = deque(maxlen=limit)
        self._last_label = ""

    def push(self, state, label=""):
        """Вызывать ДО изменения модели. state — сериализуемый снимок."""
        self._undo.append((label, state))
        self._redo.clear()
        self._last_label = label

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self, current_state):
        if not self._undo:
            return None
        label, state = self._undo.pop()
        self._redo.append((label, current_state))
        return state

    def redo(self, current_state):
        if not self._redo:
            return None
        label, state = self._redo.pop()
        self._undo.append((label, current_state))
        return state

    def clear(self):
        self._undo.clear()
        self._redo.clear()

    @property
    def depth(self):
        return len(self._undo)