/** Pretty-print a snake_case / camelCase key into Title Case */
export const humanizeKey = (key) =>
  key
    .replace(/([a-z])([A-Z])/g, '$1 $2') // camelCase
    .replace(/[_-]+/g, ' ') // snake_case / kebab
    .replace(/\b\w/g, (c) => c.toUpperCase());
