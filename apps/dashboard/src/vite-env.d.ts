/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ORIGENLAB_API_BASE_URL: string;
  /** OrigenLab's WhatsApp number (digits with country code); optional. See src/crm/company.ts. */
  readonly VITE_ORIGENLAB_WHATSAPP?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
