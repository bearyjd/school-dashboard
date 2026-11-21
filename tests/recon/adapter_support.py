"""Shared helpers for the adapter tests (synthetic data only)."""
from __future__ import annotations

import hashlib
from typing import Any

from contract_support import load

KIDS = {"alpha": "Alex", "beta": "Sam", "gamma": "Jo", "alex": "Alex", "sam": "Sam", "jo": "Jo"}
FORBIDDEN_IN_WARNINGS = ("Alpha", "Beta", "Gamma", "Alex", "Sam", "Jo", "Sample", "Worksheet",
                         "Essay", "Quiz", "Map Test", "Tester")


def resolve(name: str) -> str:
    return KIDS.get(name.strip().casefold(), name.strip().title())


def hkey(class_key: str, title_key: str, due: str) -> str:
    return "h:" + hashlib.sha1(f"{class_key}|{title_key}|{due}".encode()).hexdigest()[:16]


def contract(name: str) -> dict[str, Any]:
    return load(name)


def no_leaks(warnings: tuple[str, ...]) -> None:
    for text in warnings:
        for bad in FORBIDDEN_IN_WARNINGS:
            assert bad not in text, (bad, text)
