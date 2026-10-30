"""Focus witness that reads the browser's focus through the DevTools Protocol."""

import re
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import CDPSession, Error

# Follows focus into open shadow roots and same-origin frames so the witness
# reports the element the user sees focused, not its shadow host or <iframe>.
# A frame whose own document has nothing focused (a scrollable frame is a Tab
# stop) is reported as the <iframe>. Cross-origin frames never load: the
# request guard aborts them.
_DEEP_ACTIVE_ELEMENT = """(() => {
  let element = document.activeElement;
  while (element) {
    const frameDocument = element.contentDocument;
    const inner = element.shadowRoot?.activeElement
      ?? (frameDocument?.activeElement === frameDocument?.body
        ? null : frameDocument?.activeElement);
    if (!inner) {
      break;
    }
    element = inner;
  }
  return element;
})()"""

# Called on the focused element: samples a 3 by 3 grid over its box with
# elementFromPoint, in the element's own document or shadow root. A point is
# covered when the topmost element there is neither the focused element nor
# inside it. Like any hit test, it ignores content with pointer-events: none
# and, for an element in a frame, anything the parent page draws over it.
_COVER_SAMPLES = """function () {
  const view = this.ownerDocument.defaultView;
  const root = this.getRootNode();
  const box = this.getBoundingClientRect();
  let sampled = 0;
  let covered = 0;
  if (box.width === 0 || box.height === 0) {
    return {sampled, covered};
  }
  for (const fy of [1 / 6, 1 / 2, 5 / 6]) {
    for (const fx of [1 / 6, 1 / 2, 5 / 6]) {
      const x = box.left + box.width * fx;
      const y = box.top + box.height * fy;
      if (x < 0 || y < 0 || x >= view.innerWidth || y >= view.innerHeight) {
        continue;
      }
      sampled += 1;
      const hit = root.elementFromPoint(x, y);
      if (!hit || !this.contains(hit)) {
        covered += 1;
      }
    }
  }
  return {sampled, covered};
}"""

_ASCII_WHITESPACE = re.compile(r"[ \t\n\f\r]+")

_DOCUMENT_TAGS = frozenset({"BODY", "HTML"})
_ELEMENT_NODE = 1


@dataclass(frozen=True)
class Box:
    """Border box of an element in CSS pixels, relative to the viewport.

    Attributes:
        x: Left edge.
        y: Top edge.
        width: Width.
        height: Height.
    """

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Cover:
    """What lies on top of the focused element, sampled over its box.

    Attributes:
        sampled: Grid points inside the viewport, out of 9.
        covered: Sampled points where the topmost element is neither the
            focused element nor inside it.
    """

    sampled: int
    covered: int


@dataclass(frozen=True)
class Focus:
    """The focused element as the browser reports it.

    Attributes:
        backend_node_id: CDP backend node id, stable while the page is loaded.
        tag: Upper-case tag name, or None when nothing is focused.
        dom_id: The element's `id` attribute, if any.
        role: Accessibility role computed by the browser.
        name: Accessible name computed by the browser.
        box: Border box in the viewport, or None when the element has no box.
        cover: What covers the element, sampled on the same element object
            the other fields describe; None when focus is on the document.
        document_has_focus: Whether the document itself holds focus. It is
            False after Tab moves past the last element, when focus leaves the
            page for the browser, and stays True when a script blurs the
            focused element.
    """

    backend_node_id: int | None
    tag: str | None
    dom_id: str | None
    role: str | None
    name: str | None
    box: Box | None
    cover: Cover | None
    document_has_focus: bool

    @property
    def is_document(self) -> bool:
        """Whether focus sits on the document rather than on an element."""
        return self.tag is None or self.tag in _DOCUMENT_TAGS


class FocusWitness:
    """Reads focus and scroll position of one page through a CDP session."""

    def __init__(self, cdp: CDPSession) -> None:
        """Initializes the witness and enables the Accessibility domain.

        Args:
            cdp: A CDP session attached to the page to observe.
        """
        self._cdp = cdp
        self._cdp.send("Accessibility.enable")

    def focus(self) -> Focus:
        """Returns the element that currently has keyboard focus."""
        document_has_focus: bool = self._cdp.send(
            "Runtime.evaluate",
            {"expression": "document.hasFocus()", "returnByValue": True},
        )["result"]["value"]
        result = self._cdp.send(
            "Runtime.evaluate", {"expression": _DEEP_ACTIVE_ELEMENT}
        )["result"]
        object_id = result.get("objectId")
        if object_id is None:
            return Focus(
                backend_node_id=None,
                tag=None,
                dom_id=None,
                role=None,
                name=None,
                box=None,
                cover=None,
                document_has_focus=document_has_focus,
            )
        try:
            node = self._cdp.send("DOM.describeNode", {"objectId": object_id})["node"]
            cover = (
                None if node["nodeName"] in _DOCUMENT_TAGS else self._cover(object_id)
            )
        finally:
            self._cdp.send("Runtime.releaseObject", {"objectId": object_id})
        backend_node_id: int = node["backendNodeId"]
        role, name = self._role_and_name(backend_node_id)
        return Focus(
            backend_node_id=backend_node_id,
            tag=node["nodeName"],
            dom_id=_attribute(node, "id"),
            role=role,
            name=name,
            box=self._box(backend_node_id),
            cover=cover,
            document_has_focus=document_has_focus,
        )

    def rendered(self) -> frozenset[int]:
        """Returns the backend node ids of the elements the page renders.

        An element is rendered when it has a layout box and its computed
        visibility is `visible`; elements in same-origin frames are included.
        """
        snapshot = self._cdp.send(
            "DOMSnapshot.captureSnapshot", {"computedStyles": ["visibility"]}
        )
        strings: list[str] = snapshot["strings"]
        rendered: set[int] = set()
        for document in snapshot["documents"]:
            nodes = document["nodes"]
            layout = document["layout"]
            for index, styles in zip(
                layout["nodeIndex"], layout["styles"], strict=True
            ):
                if (
                    nodes["nodeType"][index] == _ELEMENT_NODE
                    and strings[styles[0]] == "visible"
                ):
                    rendered.add(nodes["backendNodeId"][index])
        return frozenset(rendered)

    def scroll(self) -> tuple[float, float]:
        """Returns the page's scroll offsets (x, y) in CSS pixels."""
        viewport = self._cdp.send("Page.getLayoutMetrics")["cssVisualViewport"]
        return viewport["pageX"], viewport["pageY"]

    def _cover(self, object_id: str) -> Cover:
        samples: dict[str, int] = self._cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": _COVER_SAMPLES,
                "returnByValue": True,
            },
        )["result"]["value"]
        return Cover(**samples)

    def _role_and_name(self, backend_node_id: int) -> tuple[str | None, str | None]:
        nodes = self._cdp.send(
            "Accessibility.getPartialAXTree",
            {"backendNodeId": backend_node_id, "fetchRelatives": False},
        )["nodes"]
        if not nodes:
            return None, None
        role = _ax_value(nodes[0], "role")
        name = _ax_value(nodes[0], "name")
        if name is not None:
            # Chromium builds differ in trimming names (accname trims and
            # collapses whitespace); normalizing keeps records comparable.
            name = _ASCII_WHITESPACE.sub(" ", name).strip(" ")
        return role, name

    def _box(self, backend_node_id: int) -> Box | None:
        try:
            model = self._cdp.send(
                "DOM.getBoxModel", {"backendNodeId": backend_node_id}
            )["model"]
        except Error:
            # Elements without a layout box (display: none or contents) have
            # no box model; they can still hold focus.
            return None
        xs = model["border"][0::2]
        ys = model["border"][1::2]
        return Box(
            x=min(xs), y=min(ys), width=max(xs) - min(xs), height=max(ys) - min(ys)
        )


def _attribute(node: dict[str, Any], name: str) -> str | None:
    attributes: list[str] = node.get("attributes", [])
    for key, value in zip(attributes[0::2], attributes[1::2], strict=True):
        if key == name:
            return value
    return None


def _ax_value(node: dict[str, Any], field: str) -> str | None:
    value: str | None = node.get(field, {}).get("value")
    return value
