export interface Item {
  name: string;
  price: number;
}

export class Cart {
  private items: Item[] = [];

  add(item: Item): void {
    this.items.push(item);
  }

  get total(): number {
    return sum(this.items.map((item) => item.price));
  }

  static empty(): Cart {
    return new Cart();
  }

  clear = (): void => {
    this.items = [];
  };
}

export const sum = (values: number[]): number => values.reduce((a, b) => a + b, 0);

export function formatPrice(value: number): string {
  return `$${value.toFixed(2)}`;
}

function* ids(): Generator<number> {
  let id = 0;
  while (true) yield id++;
}
