import { Plus, Trash2, Upload, Users } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCreateEmployee,
  useDeleteEmployee,
  useEmployees,
  useEnrollEmployee,
} from "@/hooks/useEmployees";
import { toast } from "sonner";

export default function EmployeesPage() {
  const { data: employees, isLoading, error } = useEmployees();
  const createEmployee = useCreateEmployee();
  const deleteEmployee = useDeleteEmployee();
  const enrollEmployee = useEnrollEmployee();

  const [addOpen, setAddOpen] = useState(false);
  const [enrollId, setEnrollId] = useState<string | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [department, setDepartment] = useState("");
  const [enrollFile, setEnrollFile] = useState<File | null>(null);
  const [enrollAngle, setEnrollAngle] = useState("front");

  const handleCreate = () => {
    createEmployee.mutate(
      { name, department },
      {
        onSuccess: () => {
          toast.success("Employee added");
          setAddOpen(false);
          setName("");
          setDepartment("");
        },
        onError: () => toast.error("Failed to add employee"),
      },
    );
  };

  const handleEnroll = () => {
    if (!enrollId || !enrollFile) {
      return;
    }
    enrollEmployee.mutate(
      { id: enrollId, file: enrollFile, angle: enrollAngle },
      {
        onSuccess: () => {
          toast.success("Photo enrolled");
          setEnrollId(null);
          setEnrollFile(null);
        },
        onError: () => toast.error("Enrollment failed"),
      },
    );
  };

  const handleDelete = () => {
    if (!deleteId) {
      return;
    }
    deleteEmployee.mutate(deleteId, {
      onSuccess: () => {
        toast.success("Employee removed");
        setDeleteId(null);
      },
      onError: () => toast.error("Failed to remove employee"),
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Employees"
        description={employees ? `${employees.length} employee${employees.length !== 1 ? "s" : ""}` : ""}
        actions={
          <Button
            onClick={() => setAddOpen(true)}
            className="gap-1.5 bg-sky-500 text-white hover:bg-sky-400"
            size="sm"
          >
            <Plus className="h-4 w-4" /> Add Employee
          </Button>
        }
      />

      {error && (
        <Alert variant="destructive" className="border-red-800 bg-red-950/40">
          <AlertDescription className="text-red-300">Failed to load employees.</AlertDescription>
        </Alert>
      )}

      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800">
              {["Name", "Department", "Enrollment", ""].map((h) => (
                <th key={h} className="px-5 py-3 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="border-b border-zinc-800/50">
                  {Array.from({ length: 4 }).map((__, j) => (
                    <td key={j} className="px-5 py-4">
                      <Skeleton className="h-4 w-full bg-zinc-800" />
                    </td>
                  ))}
                </tr>
              ))
            ) : !employees?.length ? (
              <tr>
                <td colSpan={4} className="py-16 text-center">
                  <Users className="mx-auto mb-3 h-8 w-8 text-zinc-700" />
                  <p className="text-sm text-zinc-500">No employees added yet</p>
                </td>
              </tr>
            ) : (
              employees.map((emp) => (
                <tr key={emp.id} className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-800/20">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-700 text-sm font-medium text-zinc-300">
                        {emp.name.charAt(0).toUpperCase()}
                      </div>
                      <span className="font-medium text-zinc-100">{emp.name}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3.5 text-zinc-400">{emp.department || "—"}</td>
                  <td className="px-5 py-3.5">
                    <StatusBadge status={emp.enrollment_status} />
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 gap-1.5 text-xs text-zinc-400 hover:bg-sky-500/10 hover:text-sky-400"
                        onClick={() => setEnrollId(emp.id)}
                      >
                        <Upload className="h-3 w-3" /> Enroll
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7 text-zinc-500 hover:bg-red-500/10 hover:text-red-400"
                        onClick={() => setDeleteId(emp.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Sheet open={addOpen} onOpenChange={setAddOpen}>
        <SheetContent className="w-[400px] border-zinc-800 bg-zinc-900 text-zinc-50">
          <SheetHeader>
            <SheetTitle className="text-zinc-50">Add Employee</SheetTitle>
          </SheetHeader>
          <div className="mt-6 space-y-4">
            <div className="space-y-1.5">
              <Label className="text-zinc-300">Name</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="border-zinc-700 bg-zinc-800 text-zinc-50 focus-visible:ring-sky-500"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-zinc-300">Department</Label>
              <Input
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
                className="border-zinc-700 bg-zinc-800 text-zinc-50 focus-visible:ring-sky-500"
              />
            </div>
          </div>
          <SheetFooter className="mt-8">
            <Button variant="ghost" onClick={() => setAddOpen(false)} className="text-zinc-400">
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={!name || createEmployee.isPending}
              className="bg-sky-500 text-white hover:bg-sky-400"
            >
              {createEmployee.isPending ? "Adding…" : "Add Employee"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <Sheet open={!!enrollId} onOpenChange={(o) => !o && setEnrollId(null)}>
        <SheetContent className="w-[400px] border-zinc-800 bg-zinc-900 text-zinc-50">
          <SheetHeader>
            <SheetTitle className="text-zinc-50">Enroll Photo</SheetTitle>
          </SheetHeader>
          <div className="mt-6 space-y-4">
            <div className="space-y-1.5">
              <Label className="text-zinc-300">Photo</Label>
              <Input
                type="file"
                accept="image/*"
                onChange={(e) => setEnrollFile(e.target.files?.[0] ?? null)}
                className="border-zinc-700 bg-zinc-800 text-zinc-300 file:rounded file:border-0 file:bg-zinc-700 file:px-3 file:py-1 file:text-xs file:text-zinc-300"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-zinc-300">Angle</Label>
              <Select value={enrollAngle} onValueChange={setEnrollAngle}>
                <SelectTrigger className="border-zinc-700 bg-zinc-800 text-zinc-300">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="border-zinc-700 bg-zinc-900">
                  {["front", "left", "right", "up", "down"].map((a) => (
                    <SelectItem key={a} value={a} className="capitalize text-zinc-300">
                      {a}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <SheetFooter className="mt-8">
            <Button variant="ghost" onClick={() => setEnrollId(null)} className="text-zinc-400">
              Cancel
            </Button>
            <Button
              onClick={handleEnroll}
              disabled={!enrollFile || enrollEmployee.isPending}
              className="bg-sky-500 text-white hover:bg-sky-400"
            >
              {enrollEmployee.isPending ? "Enrolling…" : "Enroll"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <AlertDialog open={!!deleteId} onOpenChange={(o) => !o && setDeleteId(null)}>
        <AlertDialogContent className="border-zinc-800 bg-zinc-900">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-zinc-50">Remove employee?</AlertDialogTitle>
            <AlertDialogDescription className="text-zinc-400">
              This will permanently remove the employee and their enrollment data.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-zinc-700 bg-transparent text-zinc-300 hover:bg-zinc-800">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-red-600 text-white hover:bg-red-500">
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
