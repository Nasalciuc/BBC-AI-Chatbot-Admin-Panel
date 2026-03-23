import { Crown, Shield, UserCheck, Headset } from 'lucide-react'

export const callTypes = new Map<string, string>([
  ['active', 'bg-teal-100/30 text-teal-900 dark:text-teal-200 border-teal-200'],
  ['inactive', 'bg-neutral-300/40 border-neutral-300'],
])

export const roles = [
  {
    label: 'Owner',
    value: 'owner',
    icon: Crown,
  },
  {
    label: 'Admin',
    value: 'admin',
    icon: Shield,
  },
  {
    label: 'Sales Agent',
    value: 'sales',
    icon: UserCheck,
  },
  {
    label: 'Support Agent',
    value: 'support',
    icon: Headset,
  },
] as const
