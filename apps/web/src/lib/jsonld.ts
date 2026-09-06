import { site } from '../config/site';
import { company } from '../data/company';
import { contact } from '../data/contact';
import type { Product } from '../data/products';
import type { Brand } from '../data/brands';
import { productPageHref } from './catalog';

/**
 * Datos estructurados. Sólo se declara lo que el repositorio puede probar:
 * ninguna oferta, precio, stock, valoración ni certificación.
 * Identidad legal (razón social, RUT) pendiente: docs/design/CONTENT_NEEDED.md.
 */

export function organizationJsonLd() {
  return {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    '@id': `${site.baseUrl}/#organization`,
    name: site.name,
    url: `${site.baseUrl}/`,
    logo: `${site.baseUrl}/logo/origenlab-lockup-light.svg`,
    description: company.oneLiner,
    email: contact.email,
    telephone: `+${contact.phoneE164}`,
    areaServed: { '@type': 'Country', name: 'Chile' },
    address: {
      '@type': 'PostalAddress',
      addressLocality: contact.city,
      addressCountry: 'CL',
    },
    contactPoint: [
      {
        '@type': 'ContactPoint',
        contactType: 'sales',
        email: contact.email,
        telephone: `+${contact.phoneE164}`,
        availableLanguage: ['es'],
        areaServed: 'CL',
      },
    ],
  };
}

export function websiteJsonLd() {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    '@id': `${site.baseUrl}/#website`,
    url: `${site.baseUrl}/`,
    name: site.name,
    inLanguage: 'es-CL',
    publisher: { '@id': `${site.baseUrl}/#organization` },
  };
}

export function faqJsonLd(items: readonly { question: string; answer: string }[]) {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: items.map((item) => ({
      '@type': 'Question',
      name: item.question,
      acceptedAnswer: { '@type': 'Answer', text: item.answer },
    })),
  };
}

export function productJsonLd(product: Product, brand: Brand | undefined) {
  const href = productPageHref(product);
  return {
    '@context': 'https://schema.org',
    '@type': 'Product',
    name: product.name,
    description: product.description ?? product.summary,
    category: product.equipmentType,
    ...(href ? { url: `${site.baseUrl}${href}/` } : {}),
    ...(product.imagePath ? { image: `${site.baseUrl}${product.imagePath}` } : {}),
    ...(product.sku ? { sku: product.sku } : {}),
    ...(brand
      ? {
          brand: { '@type': 'Brand', name: brand.name },
          manufacturer: { '@type': 'Organization', name: brand.legalName ?? brand.name },
        }
      : {}),
    additionalProperty: (product.keySpecs ?? []).map((spec) => ({
      '@type': 'PropertyValue',
      name: spec.label,
      value: spec.value,
    })),
  };
}

export function itemListJsonLd(products: readonly Product[], listName: string) {
  return {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: listName,
    numberOfItems: products.length,
    itemListElement: products.map((product, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: product.name,
      url: `${site.baseUrl}${productPageHref(product)}/`,
    })),
  };
}
