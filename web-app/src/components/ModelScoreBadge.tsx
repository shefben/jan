import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ModelScore } from '@/services/models/types'
import { Loader } from 'lucide-react'
import { FIT_LEVEL_BADGE_VARIANTS } from '@/utils/scoreUtils'

type Props = {
  score?: ModelScore
  compact?: boolean
  className?: string
}

export function ModelScoreBadge({ score, compact = false, className }: Props) {
  if (!score || score.status === 'loading') {
    return (
      <Badge variant="secondary" className={cn('gap-1', className)}>
        <Loader className="size-3 animate-spin" />
        {!compact && 'Scoring'}
      </Badge>
    )
  }
  if (score.status !== 'ready' || typeof score.overall !== 'number') {
    return compact ? null : <Badge variant="secondary" className={className}>N/A</Badge>
  }
  const fit = score.breakdown?.fit_level
  const variant = fit ? FIT_LEVEL_BADGE_VARIANTS[fit as keyof typeof FIT_LEVEL_BADGE_VARIANTS] ?? 'default' : 'default'
  return (
    <Badge variant={variant} className={cn('gap-1', className)} title={score.reason}>
      {score.overall.toFixed(1)}
      {!compact && fit ? ` · ${fit}` : ''}
    </Badge>
  )
}
