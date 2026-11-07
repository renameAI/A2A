// shadcn/ui Table (MIT) — https://ui.shadcn.com/docs/components/table
import { cn } from '@/utils.js';

export function Table({ className, ...props }) {
  return (
    <div data-slot="table-container" className="relative w-full overflow-x-auto">
      <table data-slot="table" className={cn('w-full caption-bottom text-sm', className)} {...props} />
    </div>
  );
}
export function TableHeader({ className, ...props }) {
  return <thead data-slot="table-header" className={cn('[&_tr]:border-b', className)} {...props} />;
}
export function TableBody({ className, ...props }) {
  return <tbody data-slot="table-body" className={cn('[&_tr:last-child]:border-0', className)} {...props} />;
}
export function TableRow({ className, ...props }) {
  return <tr data-slot="table-row" className={cn('hover:bg-muted/50 data-[state=selected]:bg-muted border-b transition-colors', className)} {...props} />;
}
export function TableHead({ className, ...props }) {
  return <th data-slot="table-head" className={cn('text-foreground h-10 px-2 text-left align-middle font-medium whitespace-nowrap', className)} {...props} />;
}
export function TableCell({ className, ...props }) {
  return <td data-slot="table-cell" className={cn('p-2 align-middle whitespace-nowrap', className)} {...props} />;
}
export function TableCaption({ className, ...props }) {
  return <caption data-slot="table-caption" className={cn('text-muted-foreground mt-4 text-sm', className)} {...props} />;
}
