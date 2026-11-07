// shadcn/ui Input (MIT) — https://ui.shadcn.com/docs/components/input
import { cn } from '@/utils.js';

export function Input({ className, type, ...props }) {
  return (
    <input type={type} data-slot="input"
           className={cn('placeholder:text-muted-foreground border-input h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none md:text-sm dark:bg-input/30',
                         'focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]', className)}
           {...props} />
  );
}
