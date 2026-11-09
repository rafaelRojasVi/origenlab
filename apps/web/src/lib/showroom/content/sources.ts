import { brandModels } from '../../../data/brandModels';
import { products } from '../../../data/products';

/** Valor de un criterio con fuente de `brandModels.ts`, o `undefined`. */
export function criterion(modelId: string, label: string): string | undefined {
  return brandModels.find((model) => model.id === modelId)?.criteria.find((item) => item.label === label)?.value;
}

/** El producto de `products.ts`, para leer su ficha. */
export function product(productId: string) {
  return products.find((item) => item.id === productId);
}

/** Quita los huecos de una lista de líneas. */
export function lines(...values: (string | false | null | undefined)[]): string[] {
  return values.filter((value): value is string => typeof value === 'string' && value.length > 0);
}

/** Valor de una especificación de la ficha de `products.ts`, o `undefined`. */
export function spec(productId: string, label: string): string | undefined {
  return product(productId)?.keySpecs?.find((item) => item.label === label)?.value;
}
