---
name: bbc-frontend-design
description: Design system patterns for BBC Admin Panel — Radix UI + Tailwind CSS 4 + CVA component conventions
applyTo: "src/components/**,src/features/**"
---

# BBC Frontend Design Skill

## Purpose

Guide Claude to build UI components that follow the BBC Admin Panel's design system: Radix UI primitives styled with Tailwind CSS 4 and composed via CVA (class-variance-authority).

## Component Anatomy

Every component follows this structure:

```tsx
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const widgetVariants = cva(
  'base-classes-here rounded-md font-medium',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground',
        outline: 'border border-input bg-background',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        default: 'h-10 px-4 text-sm',
        lg: 'h-12 px-6 text-base',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
)

interface WidgetProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof widgetVariants> {}

export function Widget({ className, variant, size, ...props }: WidgetProps) {
  return (
    <div className={cn(widgetVariants({ variant, size }), className)} {...props} />
  )
}
```

## Rules

1. **Radix first** — check `src/components/ui/` before adding any new component. If a Radix primitive exists, wrap it; never re-invent.
2. **CVA for variants** — every component with visual states must use `cva()`.
3. **`cn()` for merging** — always merge classNames via `cn()` from `@/lib/utils`.
4. **Tailwind only** — no inline styles, no CSS modules, no styled-components.
5. **Color tokens** — use semantic tokens (`bg-primary`, `text-muted-foreground`, `border-input`), never raw hex/rgb.
6. **Dark mode** — handled via CSS variables in the theme provider. Never use `dark:` prefix manually; tokens auto-switch.
7. **Responsive** — mobile-first: `sm:`, `md:`, `lg:` breakpoints. Test at 375px minimum.
8. **Accessibility** — all interactive elements need proper ARIA roles. Radix handles most; verify with keyboard navigation.

## Layout Patterns

### Page layout

```tsx
<Layout>
  <Layout.Header>
    <Search />
    <ThemeSwitch />
    <ProfileDropdown />
  </Layout.Header>
  <Layout.Body>
    {/* Feature content */}
  </Layout.Body>
</Layout>
```

### Data table page

```tsx
<Layout.Body>
  <div className="mb-2 flex flex-wrap items-center justify-between gap-x-4">
    <div>
      <h2 className="text-2xl font-bold tracking-tight">Title</h2>
      <p className="text-muted-foreground">Description</p>
    </div>
    <div className="flex gap-2">
      {/* Action buttons */}
    </div>
  </div>
  <div className="-mx-4 flex-1 overflow-auto px-4 py-1 lg:flex-row lg:space-x-12 lg:space-y-0">
    <DataTable columns={columns} data={data} />
  </div>
</Layout.Body>
```

### Dialog / Sheet pattern

```tsx
import { useDialogState } from '@/hooks/use-dialog-state'

const [dialogOpen, setDialogOpen] = useDialogState<ItemType | null>(null)

<Dialog open={!!dialogOpen} onOpenChange={() => setDialogOpen(null)}>
  <DialogContent>
    {/* Form content */}
  </DialogContent>
</Dialog>
```

## File Naming

- Components: `kebab-case.tsx` (e.g., `lead-card.tsx`)
- Types alongside component or in `types.ts` within feature folder
- Hooks: `use-<name>.ts` (e.g., `use-lead-filters.ts`)

## Do NOT

- Import from `@radix-ui/react-*` directly in feature code — always go through `src/components/ui/`.
- Use `useState` for server data — use React Query.
- Create global CSS classes — use Tailwind utilities.
- Hard-code spacing values — use Tailwind's spacing scale.
