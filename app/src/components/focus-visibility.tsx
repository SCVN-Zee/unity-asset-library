import { useLayoutEffect } from "react";
import { useFocusVisible } from "@react-aria/interactions";

// Chromium treats Command alone as keyboard input. Use React Aria's modality
// tracking so app switching cannot outline a pointer-focused control.
export function FocusVisibility() {
  const { isFocusVisible } = useFocusVisible();
  useLayoutEffect(() => {
    document.documentElement.dataset.focusVisible = String(isFocusVisible);
    return () => { delete document.documentElement.dataset.focusVisible; };
  }, [isFocusVisible]);
  return null;
}
