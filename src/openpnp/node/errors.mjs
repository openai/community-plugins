export class PluginError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'PluginError';
    this.code = code;
    this.details = details;
  }
}

export function publicError(error) {
  if (error instanceof PluginError || error?.name === 'DomainError') {
    return { code: error.code, message: error.message, details: error.details ?? {} };
  }
  return { code: 'INTERNAL_ERROR', message: 'The operation failed. Consult local diagnostics; no automatic retry was made.' };
}
