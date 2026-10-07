import type { CSSProperties } from "react";
import {
  ArrowRight,
  ArrowUp,
  Bell,
  Bot,
  Building2,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock,
  CreditCard,
  Download,
  Ellipsis,
  ExternalLink,
  FolderOpen,
  Info,
  ListChecks,
  LogOut,
  MessageCircle,
  MessagesSquare,
  Monitor,
  Moon,
  Network,
  PanelLeft,
  Pencil,
  Plug,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  Trash2,
  UserRound,
  X,
} from "lucide-react";

// Named imports keep only the icons used by the workspace in the bundle.
const icons = {
  brain: Bot,
  tasks: ListChecks,
  plus: Plus,
  search: Search,
  chat: MessageCircle,
  settings: Settings,
  help: CircleHelp,
  chevron: ChevronRight,
  down: ChevronDown,
  arrow: ArrowRight,
  send: ArrowUp,
  check: Check,
  close: X,
  info: Info,
  link: ExternalLink,
  more: Ellipsis,
  panel: PanelLeft,
  clock: Clock,
  shield: ShieldCheck,
  sun: Sun,
  moon: Moon,
  monitor: Monitor,
  trash: Trash2,
  edit: Pencil,
  download: Download,
  logout: LogOut,
  general: SlidersHorizontal,
  profile: UserRound,
  connections: Plug,
  notifications: Bell,
  workspace: Building2,
  billing: CreditCard,
  crm: Network,
  messages: MessagesSquare,
  files: FolderOpen,
} as const;

export type IconName = keyof typeof icons;

export function Icon({
  name,
  size = 20,
  style,
  className = "",
}: {
  name: IconName;
  size?: number;
  style?: CSSProperties;
  className?: string;
}) {
  const Glyph = icons[name];
  return (
    <Glyph
      aria-hidden="true"
      size={size}
      strokeWidth={1.65}
      className={className}
      style={style}
    />
  );
}
