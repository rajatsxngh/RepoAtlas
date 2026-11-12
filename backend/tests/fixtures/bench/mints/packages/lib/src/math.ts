export function add(a: number, b: number): number {
  return a + b;
}

export function sum(values: number[]): number {
  return values.reduce((acc, v) => add(acc, v), 0);
}

export class Counter {
  total = 0;

  bump(): number {
    this.total = add(this.total, 1);
    return this.total;
  }
}

export const triple = (v: number): number => add(add(v, v), v);
