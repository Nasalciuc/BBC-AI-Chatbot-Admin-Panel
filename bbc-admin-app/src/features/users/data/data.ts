import { Crown, Shield, UserCheck, Headset, Eye, ClipboardCheck, Briefcase } from 'lucide-react'

export const callTypes = new Map<string, string>([
  ['active', 'bg-teal-100/30 text-teal-900 dark:text-teal-200 border-teal-200'],
  ['inactive', 'bg-neutral-300/40 border-neutral-300'],
  ['invited', 'bg-amber-100/30 text-amber-900 dark:text-amber-200 border-amber-200'],
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
  {
    label: 'Supervisor',
    value: 'supervisor',
    icon: Eye,
  },
  {
    label: 'Project Manager',
    value: 'project_manager',
    icon: Briefcase,
  },
  {
    label: 'QA Auditor',
    value: 'qa',
    icon: ClipboardCheck,
  },
] as const
