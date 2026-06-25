// go-cov — doc-comment coverage for Go, via the stdlib go/ast parser (a real parser,
// not regex). Prints "<documented> <total>" for the exported symbols across all file
// args. Single-file syntactic parse: unresolved imports/missing sibling files don't
// matter (unlike `go doc`, which must build the package). Files that don't parse are
// skipped so the rest still count. Invoked by bin/evergreen-scan via `go run`.
package main

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
)

func main() {
	doc, tot := 0, 0
	fset := token.NewFileSet()
	for _, fn := range os.Args[1:] {
		f, err := parser.ParseFile(fset, fn, nil, parser.ParseComments)
		if err != nil {
			continue
		}
		for _, d := range f.Decls {
			switch decl := d.(type) {
			case *ast.FuncDecl: // exported funcs AND methods (Recv != nil)
				if ast.IsExported(decl.Name.Name) {
					tot++
					if decl.Doc != nil {
						doc++
					}
				}
			case *ast.GenDecl: // type declarations (parity with the regex heuristic)
				for _, spec := range decl.Specs {
					ts, ok := spec.(*ast.TypeSpec)
					if !ok || !ast.IsExported(ts.Name.Name) {
						continue
					}
					tot++
					// A single-spec `type Foo …` carries its doc on the GenDecl;
					// a grouped `type ( … )` carries it on each TypeSpec.
					if ts.Doc != nil || (len(decl.Specs) == 1 && decl.Doc != nil) {
						doc++
					}
				}
			}
		}
	}
	fmt.Printf("%d %d\n", doc, tot)
}
