export function journalName(value: string): string {
  return value.toLocaleLowerCase('en-US').replace(/(^|[\s\-–—/])([a-z])/g, (_, separator: string, letter: string) => separator + letter.toUpperCase());
}
