const endpoint = "cdn.widget.io";       // POSITIVE
const sheet = "styles.css";             // NEGATIVE (file)
const localName = "WidgetList";         // NEGATIVE (no dot)
const prop = "avatar.url";              // RULE-MATCH, FILTER-DROP (property path)
const member = "pj.id";                 // RULE-MATCH, FILTER-DROP (member access)
const event = "mouseleave.bs.carousel"; // RULE-MATCH, FILTER-DROP (library event)
