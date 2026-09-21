"""
Finite State Machine (FSM) state definitions for the English Card Bot.

This module defines all possible states for user interactions,
including quiz answering, word addition, and word deletion flows.
"""

from aiogram.fsm.state import State, StatesGroup


class MyStates(StatesGroup):
    """
    FSM states for managing user interaction flows.

    These states control the multi-step processes for quiz participation,
    adding new words to the user's dictionary, and deleting existing words.
    """

    waiting_for_russian_word = State()
    waiting_for_translation_english = State()
    waiting_for_word_to_delete = State()
    waiting_for_quiz_answer = State()
