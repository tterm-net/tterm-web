// Lets xterm.js style itself under the page's strict security policy.
//
// The policy allows styles only from the page's own files, so a style
// element slipped into the page by anyone else is ignored. xterm.js,
// though, writes a few small style elements of its own (the scrollbar, the
// row sizes). The core therefore puts a one-time number into each copy of
// the page it serves, in a meta tag, and allows style elements that carry
// it; here every style element the page creates gets that number. Inline
// styles in general stay forbidden: nothing else can guess the number.

const meta = document.querySelector<HTMLMetaElement>(
  'meta[name="tt-style-nonce"]');
const nonce = meta?.content ?? "";

if (nonce) {
  const create = Document.prototype.createElement;
  Document.prototype.createElement = function (
    this: Document, tagName: string, options?: ElementCreationOptions,
  ): HTMLElement {
    const element = create.call(this, tagName, options);
    if (tagName.toLowerCase() === "style") element.setAttribute("nonce", nonce);
    return element;
  } as typeof Document.prototype.createElement;
  meta?.remove();
}

export {};
