# Example Python file for workspace demo
import os
import sys


def hello(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


class Calculator:
    """A simple calculator class."""

    def __init__(self):
        self.history = []

    def add(self, a: float, b: float) -> float:
        result = a + b
        self.history.append(f"{a} + {b} = {result}")
        return result

    def subtract(self, a: float, b: float) -> float:
        result = a - b
        self.history.append(f"{a} - {b} = {result}")
        return result


if __name__ == "__main__":
    calc = Calculator()
    print(calc.add(2, 3))
    print(hello("World"))
