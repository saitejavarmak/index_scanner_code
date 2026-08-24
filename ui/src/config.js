// In production (Docker), the API is served from the same origin.
// In development, it's on localhost:5001.
export const API_BASE = import.meta.env.DEV ? 'http://localhost:5001' : ''
