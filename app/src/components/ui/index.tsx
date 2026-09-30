import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type CSSProperties,
  type ForwardRefExoticComponent,
  type HTMLAttributes,
  type MouseEventHandler,
  type PointerEventHandler,
  type ReactNode,
  type Ref,
  type RefAttributes,
  type RefObject,
} from "react";
import { createAlert } from "@gluestack-ui/core/alert/creator";
import { createAlertDialog } from "@gluestack-ui/core/alert-dialog/creator";
import { createButton } from "@gluestack-ui/core/button/creator";
import { createInput } from "@gluestack-ui/core/input/creator";
import { UIIcon } from "@gluestack-ui/core/icon/creator";
import type { LucideIcon, LucideProps } from "lucide-react";
import { createPopover } from "@gluestack-ui/core/popover/creator";

export { OverlayProvider } from "@gluestack-ui/core/overlay/creator";

export const Icon = UIIcon as unknown as Component<
  SVGSVGElement,
  LucideProps & { as: LucideIcon }
>;

// Core owns interaction, state, focus and accessibility. These web-only hosts
// translate its React Native host vocabulary into DOM props; styles.css owns the theme.
type HostStyle =
  | CSSProperties
  | Record<string, unknown>
  | HostStyle[]
  | undefined;
type HostProps = Omit<HTMLAttributes<HTMLElement>, "style"> & {
  style?: HostStyle;
  states?: unknown;
  importantForAccessibility?: unknown;
  accessibilityElementsHidden?: unknown;
  accessibilityViewIsModal?: unknown;
  onAccessibilityEscape?: unknown;
  accessible?: unknown;
  accessibilityRole?: unknown;
  accessibilityLabel?: unknown;
  collapsable?: unknown;
  pointerEvents?: unknown;
  entering?: unknown;
  exiting?: unknown;
  initial?: unknown;
  animate?: unknown;
  exit?: unknown;
  isInvalid?: unknown;
  isReadOnly?: unknown;
  isRequired?: unknown;
  secureTextEntry?: boolean;
  type?: ComponentPropsWithoutRef<"input">["type"];
  dataSet?: Record<string, string | number | boolean | undefined>;
  onPress?: MouseEventHandler<HTMLElement>;
  onPressIn?: PointerEventHandler<HTMLElement>;
  onPressOut?: PointerEventHandler<HTMLElement>;
  onHoverIn?: MouseEventHandler<HTMLElement>;
  onHoverOut?: MouseEventHandler<HTMLElement>;
  onChangeText?: (text: string) => void;
  editable?: boolean;
  readOnly?: boolean;
  disabled?: boolean;
  isDisabled?: boolean;
  value?: string;
  label?: string;
};

function domStyle(style: HostProps["style"]): CSSProperties | undefined {
  if (!style) return undefined;
  const result = Array.isArray(style)
    ? (Object.assign({}, ...style.map(domStyle)) as Record<string, unknown>)
    : style;
  if (!Array.isArray(result.transform)) return result as CSSProperties;
  return {
    ...result,
    transform: result.transform
      .map((transform: Record<string, string | number | number[]>) =>
        Object.entries(transform)
          .map(([name, value]) => {
            const unit =
              typeof value === "number" && /^(translate|perspective)/.test(name)
                ? "px"
                : "";
            return `${name}(${Array.isArray(value) ? value.join(",") : `${value}${unit}`})`;
          })
          .join(" "),
      )
      .join(" "),
  } as CSSProperties;
}

function domProps(props: HostProps) {
  const {
    states: _states,
    dataSet,
    style,
    onPress,
    onPressIn,
    onPressOut,
    onHoverIn,
    onHoverOut,
    onChangeText: _onChangeText,
    editable: _editable,
    secureTextEntry: _secure,
    importantForAccessibility: _important,
    accessibilityElementsHidden: _hidden,
    accessibilityViewIsModal: _modal,
    onAccessibilityEscape: _escape,
    accessible: _accessible,
    accessibilityRole: _role,
    accessibilityLabel: _label,
    collapsable: _collapsable,
    pointerEvents: _pointerEvents,
    entering: _entering,
    exiting: _exiting,
    initial: _initial,
    animate: _animate,
    exit: _exit,
    isDisabled: _disabled,
    isInvalid: _invalid,
    isReadOnly: _readOnly,
    isRequired: _required,
    ...rest
  } = props;
  const attributes: Record<string, string | number | undefined> = {};
  for (const key in dataSet) {
    const value = dataSet[key];
    attributes[`data-${key}`] =
      typeof value === "boolean" ? String(value) : value;
  }
  return {
    ...rest,
    ...attributes,
    style: domStyle(style),
    onClick: rest.onClick ?? onPress,
    onPointerDown: rest.onPointerDown ?? onPressIn,
    onPointerUp: rest.onPointerUp ?? onPressOut,
    onMouseEnter: rest.onMouseEnter ?? onHoverIn,
    onMouseLeave: rest.onMouseLeave ?? onHoverOut,
  };
}

const ViewHost = forwardRef<HTMLDivElement, HostProps>((props, ref) => (
  <div {...domProps(props)} ref={ref} />
));
const TextHost = forwardRef<HTMLSpanElement, HostProps>((props, ref) => (
  <span {...domProps(props)} ref={ref} />
));
const ButtonHost = forwardRef<HTMLButtonElement, HostProps>((props, ref) => {
  const { type, ...rest } = props;
  return (
    <button
      {...domProps(rest)}
      type={
        type === "submit" ? "submit" : type === "reset" ? "reset" : "button"
      }
      ref={ref}
    />
  );
});
const IconHost = forwardRef<HTMLSpanElement, HostProps>((props, ref) => (
  <span {...domProps(props)} ref={ref} aria-hidden="true" />
));
const SpinnerHost = forwardRef<HTMLSpanElement, HostProps>((props, ref) => (
  <span role="status" aria-label="Loading" {...domProps(props)} ref={ref} />
));
const InputHost = forwardRef<HTMLInputElement, HostProps>((props, ref) => (
  <input
    {...domProps(props)}
    ref={ref}
    readOnly={props.editable === false || props.readOnly}
    type={props.secureTextEntry ? "password" : props.type}
    onChange={(event) => {
      props.onChange?.(event);
      props.onChangeText?.(event.currentTarget.value);
    }}
  />
));


type Component<Element, Props> = ForwardRefExoticComponent<
  Props & RefAttributes<Element>
>;
type PartProps = HTMLAttributes<HTMLDivElement>;
type ButtonProps = ComponentPropsWithoutRef<"button"> & {
  onPress?: MouseEventHandler<HTMLButtonElement>;
  isDisabled?: boolean;
};
type InputProps = PartProps & {
  isDisabled?: boolean;
  isInvalid?: boolean;
  isReadOnly?: boolean;
  isRequired?: boolean;
};
type InputFieldProps = ComponentPropsWithoutRef<"input"> & {
  onChangeText?: (text: string) => void;
};

type OverlayProps = PartProps & {
  isOpen?: boolean;
  onClose?: () => void;
  initialFocusRef?: RefObject<HTMLButtonElement | null>;
  finalFocusRef?: RefObject<HTMLElement | null>;
  isKeyboardDismissable?: boolean;
};
type TriggerProps = ButtonProps & { ref?: Ref<HTMLButtonElement> };
type PopoverProps = OverlayProps & {
  onOpen?: () => void;
  placement?: "bottom" | "bottom right" | "bottom left" | "top";
  offset?: number;
  trigger: (props: TriggerProps) => ReactNode;
};

// The factory declarations are React-Native-shaped. Narrow only the exported
// host contracts here; no forwarding wrappers or application logic are needed.
const CoreButton = createButton({
  Root: ButtonHost,
  Text: TextHost,
  Group: ViewHost,
  Spinner: SpinnerHost,
  Icon: IconHost,
});
export const Button = CoreButton as unknown as Component<
  HTMLButtonElement,
  ButtonProps
>;
const CoreInput = createInput({
  Root: ViewHost,
  Icon: IconHost,
  Slot: ButtonHost,
  Input: InputHost,
});
export const Input = CoreInput as unknown as Component<
  HTMLDivElement,
  InputProps
>;
export const InputField = CoreInput.Input as unknown as Component<
  HTMLInputElement,
  InputFieldProps
>;



const CorePopover = createPopover({
  Root: ViewHost,
  Arrow: ViewHost,
  Content: ViewHost,
  Header: ViewHost,
  Footer: ViewHost,
  Body: ViewHost,
  Backdrop: ViewHost,
  CloseButton: ButtonHost,
});
export const Popover = CorePopover as unknown as Component<
  HTMLDivElement,
  PopoverProps
>;
export const PopoverContent = CorePopover.Content as unknown as Component<
  HTMLDivElement,
  PartProps
>;

const CoreDialog = createAlertDialog({
  Root: ViewHost,
  Content: ViewHost,
  CloseButton: ButtonHost,
  Header: ViewHost,
  Footer: ViewHost,
  Body: ViewHost,
  Backdrop: ViewHost,
});
export const AlertDialog = CoreDialog as unknown as Component<
  HTMLDivElement,
  OverlayProps & { closeOnOverlayClick?: boolean }
>;
export const AlertDialogContent = CoreDialog.Content as unknown as Component<
  HTMLDivElement,
  PartProps
>;
export const AlertDialogBackdrop = CoreDialog.Backdrop as unknown as Component<
  HTMLDivElement,
  PartProps
>;
export const AlertDialogHeader = CoreDialog.Header as unknown as Component<
  HTMLDivElement,
  PartProps
>;
export const AlertDialogBody = CoreDialog.Body as unknown as Component<
  HTMLDivElement,
  PartProps
>;
export const AlertDialogFooter = CoreDialog.Footer as unknown as Component<
  HTMLDivElement,
  PartProps
>;
export const Alert = createAlert({
  Root: ViewHost,
  Text: TextHost,
  Icon: IconHost,
}) as unknown as Component<HTMLDivElement, PartProps>;
