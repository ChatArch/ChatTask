"""Bounded polynomial example; JSON coefficients are data, never Python code."""
import argparse
import json
from pathlib import Path

import sympy


def calculate(values):
    coefficients = values.get("coefficients") if isinstance(values, dict) else None
    if (not isinstance(coefficients, list) or not 1 <= len(coefficients) <= 32
            or any(type(value) is not int or abs(value) > 1_000_000 for value in coefficients)):
        raise ValueError("Expected 1–32 integer coefficients, each within ±1000000")
    x = sympy.Symbol("x")
    polynomial = sum(value * x ** power for power, value in enumerate(reversed(coefficients)))
    return {
        "engine": "sympy", "engine_version": sympy.__version__,
        "polynomial": str(polynomial), "factorized": str(sympy.factor(polynomial)),
        "derivative": str(sympy.diff(polynomial, x)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        result = calculate({"coefficients": [1, 0, -1]})
        assert result == {"engine": "sympy", "engine_version": "1.14.0", "polynomial": "x**2 - 1",
                          "factorized": "(x - 1)*(x + 1)", "derivative": "2*x"}
    else:
        if args.input is None or args.output is None:
            parser.error("Select --self-test or both --input and --output")
        if args.input.stat().st_size > 4096:
            parser.error("Input must be at most 4096 bytes")
        result = calculate(json.loads(args.input.read_text()))
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
