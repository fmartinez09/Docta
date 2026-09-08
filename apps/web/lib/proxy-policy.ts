const id = "[0-9a-fA-F-]{36}";
const allowed = [
  ["GET", /^courses$/],
  ["POST", /^courses$/],
  ["GET", new RegExp(`^courses/${id}$`)],
  ["GET", new RegExp(`^courses/${id}/documents$`)],
  ["POST", new RegExp(`^courses/${id}/documents/uploads$`)],
  ["GET", new RegExp(`^courses/${id}/documents/${id}/versions/${id}$`)],
  [
    "POST",
    new RegExp(`^courses/${id}/documents/${id}/versions/${id}/complete$`),
  ],
  ["POST", new RegExp(`^courses/${id}/corpus/activate$`)],
  ["GET", new RegExp(`^courses/${id}/conversations$`)],
  ["POST", new RegExp(`^courses/${id}/conversations$`)],
  ["GET", new RegExp(`^conversations/${id}$`)],
  ["GET", new RegExp(`^conversations/${id}/messages/${id}$`)],
  ["POST", new RegExp(`^conversations/${id}/messages$`)],
] as const;

export function allowedProxyPath(method: string, path: string) {
  return allowed.some(
    ([verb, pattern]) => verb === method && pattern.test(path),
  );
}
