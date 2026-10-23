import React from "react";
import { Select } from "@base-ui/react/select";
import { Check, ChevronDown } from "lucide-react";

// Render the popup in the document, rather than delegating its colors and
// geometry to Qt's platform-native <select> popup.
export function ThemedSelect({ label, name, value, options, onChange, disabled = false, className = "" }) {
  const selected = options.find(([key]) => String(key) === String(value));
  return <Select.Root name={name} value={value} disabled={disabled}
    items={options.map(([key, text]) => ({ value: key, label: text }))}
    onValueChange={next => { if (next !== null) onChange?.(next); }}>
    <Select.Trigger className={`themed-select-trigger ${className}`} aria-label={label}>
      <Select.Value>{selected?.[1] ?? (value ? `Unavailable: ${value}` : "Choose an option")}</Select.Value>
      <Select.Icon><ChevronDown size={15} aria-hidden="true" /></Select.Icon>
    </Select.Trigger>
    <Select.Portal>
      <Select.Positioner className="themed-select-positioner" sideOffset={5} alignItemWithTrigger={false}>
        <Select.Popup className="themed-select-popup">
          <Select.ScrollUpArrow className="themed-select-scroll-arrow">⌃</Select.ScrollUpArrow>
          <Select.List className="themed-select-list">
            {options.map(([key, text, unavailable = false]) => <Select.Item className="themed-select-item" key={String(key)} value={key} disabled={unavailable}>
              <Select.ItemText>{text}</Select.ItemText>
              <Select.ItemIndicator><Check size={14} aria-hidden="true" /></Select.ItemIndicator>
            </Select.Item>)}
          </Select.List>
          <Select.ScrollDownArrow className="themed-select-scroll-arrow">⌄</Select.ScrollDownArrow>
        </Select.Popup>
      </Select.Positioner>
    </Select.Portal>
  </Select.Root>;
}
