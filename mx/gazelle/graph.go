// Package gazelle extends the Java source index with mx project boundaries.
package gazelle

import (
	"encoding/json"
	"flag"
	"fmt"
	"maps"
	"os"
	"path"
	"path/filepath"
	"slices"
	"sort"
	"strings"

	java "github.com/bazel-contrib/rules_jvm/java/gazelle"
	"github.com/bazel-contrib/rules_jvm/java/gazelle/javaconfig"
	"github.com/bazelbuild/bazel-gazelle/config"
	"github.com/bazelbuild/bazel-gazelle/label"
	"github.com/bazelbuild/bazel-gazelle/language"
	"github.com/bazelbuild/bazel-gazelle/repo"
	"github.com/bazelbuild/bazel-gazelle/resolve"
	"github.com/bazelbuild/bazel-gazelle/rule"
)

type project struct {
	Name                                 string
	Release                              int
	Options, Dirs, Packages, Tests, Deps []string
	Sources, Resources, Processors       []string
	JVMCI                                bool
	ABIAfterJavac                        bool `json:"abi_after_javac"`
	Shade                                string
	ShadeInputs                          []string `json:"shade_inputs"`
	AddExports                           []string `json:"add_exports"`
	ResourceRoot                         string   `json:"resource_root"`
	Testonly                             bool
}

type javaLanguage interface {
	language.Language
	language.LifecycleManager
}

type importer struct {
	javaLanguage
	filename, suite string
	owners          map[string]*project
	projects        map[string]*project
	rules           map[string][]*rule.Rule
}

func NewLanguage() language.Language {
	return &importer{javaLanguage: java.NewLanguage().(javaLanguage)}
}

func (g *importer) RegisterFlags(fs *flag.FlagSet, cmd string, c *config.Config) {
	g.javaLanguage.RegisterFlags(fs, cmd, c)
	fs.StringVar(&g.filename, "mx_graph", "", "Repository-relative mx bazel-buildfile --gazelle JSON")
}

func (g *importer) CheckFlags(fs *flag.FlagSet, c *config.Config) error {
	if err := g.javaLanguage.CheckFlags(fs, c); err != nil || g.filename == "" {
		return err
	}
	if !filepath.IsLocal(g.filename) {
		return fmt.Errorf("-mx_graph must be repository-relative")
	}
	data, err := os.ReadFile(filepath.Join(c.RepoRoot, g.filename))
	if err != nil {
		return err
	}
	var graph struct {
		Version       int
		Projects      []*project
		Distributions []struct {
			Manifest, Overlays         map[string]string
			Name, Module               string
			Deps, Contents, Directives []string
			AddExports                 []string `json:"add_exports"`
			SnippetsPattern            string   `json:"snippets_pattern"`
			ArchivedLibraries          []string `json:"archived_libraries"`
			GraalServices              bool     `json:"graal_services"`
			TruffleServices            bool     `json:"truffle_services"`
			JNIServices                bool     `json:"jni_services"`
			Release                    int
			Testonly                   bool
		}
		Layouts []struct {
			Name             string
			Literals, Copies map[string]string
			Testonly         bool
		}
		Plugins []struct {
			Name           string
			ProcessorClass string `json:"processor_class"`
			GeneratesAPI   bool   `json:"generates_api"`
			Deps           []string
		}
	}
	if err = json.Unmarshal(data, &graph); err != nil {
		return err
	}
	if graph.Version != 1 {
		return fmt.Errorf("unsupported mx graph version %d", graph.Version)
	}
	g.suite = path.Dir(filepath.ToSlash(g.filename))
	if g.suite == "." {
		g.suite = ""
	}
	g.owners = map[string]*project{}
	g.projects = map[string]*project{}
	g.rules = map[string][]*rule.Rule{}
	seen := map[label.Label]bool{}
	add := func(pkg string, r *rule.Rule) error {
		key := label.New("", pkg, r.Name())
		if seen[key] {
			return fmt.Errorf("duplicate mx target %s", key)
		}
		seen[key] = true
		g.rules[pkg] = append(g.rules[pkg], r)
		return nil
	}
	// Explicit filegroups keep whole-project compilations valid even when
	// source directories are already Bazel packages.
	groupFiles := func(p *project, attr string, files []string) ([]string, error) {
		grouped := map[string][]string{}
		for _, file := range files {
			if !filepath.IsLocal(file) {
				return nil, fmt.Errorf("invalid %s in %s: %q", attr, p.Name, file)
			}
			pkg := path.Dir(path.Join(g.suite, file))
			if pkg == "." {
				pkg = ""
			}
			grouped[pkg] = append(grouped[pkg], path.Base(file))
		}
		var labels []string
		for pkg, members := range grouped {
			r := rule.NewRule("mx_files", p.Name+"."+attr)
			r.SetAttr("srcs", members)
			r.SetAttr("visibility", []string{"//" + g.suite + ":__pkg__"})
			if err := add(pkg, r); err != nil {
				return nil, err
			}
			labels = append(labels, label.New("", pkg, r.Name()).String())
		}
		sort.Strings(labels)
		return labels, nil
	}
	for _, p := range graph.Projects {
		if p == nil || p.Name == "" || len(p.Dirs) == 0 || p.Release < 1 {
			return fmt.Errorf("%s: expected a Java project with a name, source directories and release", g.filename)
		}
		g.projects[p.Name] = p
		for _, dir := range p.Dirs {
			if !filepath.IsLocal(dir) {
				return fmt.Errorf("invalid directory %q for %s", dir, p.Name)
			}
			pkg := path.Join(g.suite, dir)
			if prev := g.owners[pkg]; prev != nil {
				return fmt.Errorf("%s and %s both own %s", prev.Name, p.Name, pkg)
			}
			g.owners[pkg] = p
		}
		var r *rule.Rule
		if len(p.Sources) != 0 || p.Shade != "" {
			kind, opts := "mx_whole_java", "javac_opts"
			if p.Shade != "" {
				kind, opts = "mx_shaded_java", "javacopts"
			} else if p.JVMCI {
				kind, opts = "mx_jvmci_library", "javacopts"
			} else if len(p.Processors) != 0 || len(p.AddExports) != 0 {
				kind, opts = "mx_processed_java", "javacopts"
			}
			r = rule.NewRule(kind, p.Name)
			r.SetAttr(opts, p.Options)
			if p.Shade != "" {
				r.SetAttr("shade", p.Shade)
				r.SetAttr("shade_inputs", p.ShadeInputs)
				r.SetAttr("release", p.Release)
			} else if len(p.AddExports) != 0 {
				r.SetAttr("add_exports", p.AddExports)
			}
			if len(p.Processors) != 0 {
				r.SetAttr("plugins", p.Processors)
				if p.ABIAfterJavac && !p.JVMCI {
					r.SetAttr("abi_after_javac", true)
				}
			} else if kind == "mx_whole_java" {
				r.SetAttr("unified_release", p.Release)
			}
			if p.Testonly {
				r.SetAttr("testonly", true)
			}
			for _, attr := range []struct {
				name  string
				files []string
			}{{"srcs", p.Sources}, {"resources", p.Resources}} {
				if len(attr.files) == 0 {
					continue
				}
				labels, err := groupFiles(p, attr.name, attr.files)
				if err != nil {
					return err
				}
				r.SetAttr(attr.name, labels)
			}
			if p.ResourceRoot != "" {
				r.SetAttr("mx_resource_root", p.ResourceRoot)
			}
		} else {
			r = rule.NewRule("mx_project", p.Name)
		}
		if len(p.Deps) > 0 {
			r.SetAttr("deps", p.Deps)
		}
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(g.suite, r); err != nil {
			return err
		}
		for _, class := range p.Tests {
			t := rule.NewRule("mx_test_class", p.Name+"."+class)
			t.SetAttr("project", ":"+p.Name)
			t.SetAttr("test_class", class)
			if err := add(g.suite, t); err != nil {
				return err
			}
		}
	}
	for _, dist := range graph.Distributions {
		r := rule.NewRule("mx_classpath_dist", dist.Name)
		if len(dist.Contents) > 0 {
			r.SetKind("mx_jar_dist")
			r.SetAttr("contents", dist.Contents)
			if len(dist.Overlays) > 0 {
				r.SetAttr("overlays", dist.Overlays)
			}
			if len(dist.Manifest) > 0 {
				r.SetAttr("manifest", dist.Manifest)
			}
			if dist.Module != "" {
				r.SetAttr("module_name", dist.Module)
				r.SetAttr("directives", dist.Directives)
				r.SetAttr("release", dist.Release)
			}
			if dist.SnippetsPattern != "" {
				r.SetAttr("snippets_pattern", dist.SnippetsPattern)
			}
			if len(dist.ArchivedLibraries) != 0 {
				r.SetAttr("archived_libraries", dist.ArchivedLibraries)
			}
			if dist.GraalServices {
				r.SetAttr("graal_services", true)
			}
			if dist.TruffleServices {
				r.SetAttr("truffle_services", true)
			}
			if dist.JNIServices {
				r.SetAttr("jni_services", true)
			}
			if len(dist.AddExports) > 0 {
				r.SetAttr("add_exports", dist.AddExports)
			}
		}
		if len(dist.Deps) > 0 {
			r.SetAttr("deps", dist.Deps)
		}
		r.SetAttr("visibility", []string{"//visibility:public"})
		if dist.Testonly {
			r.SetAttr("testonly", true)
		}
		if err := add(g.suite, r); err != nil {
			return err
		}
	}
	for _, layout := range graph.Layouts {
		r := rule.NewRule("mx_resource_layout", layout.Name)
		if len(layout.Literals) != 0 {
			r.SetAttr("literals", layout.Literals)
		}
		if len(layout.Copies) != 0 {
			r.SetAttr("copies", layout.Copies)
		}
		r.SetAttr("visibility", []string{"//visibility:public"})
		if layout.Testonly {
			r.SetAttr("testonly", true)
		}
		if err := add(g.suite, r); err != nil {
			return err
		}
	}
	for _, plugin := range graph.Plugins {
		r := rule.NewRule("mx_processor", plugin.Name)
		r.SetAttr("processor_class", plugin.ProcessorClass)
		r.SetAttr("visibility", []string{"//visibility:public"})
		r.SetAttr("generates_api", plugin.GeneratesAPI)
		r.SetAttr("deps", plugin.Deps)
		if err := add(g.suite, r); err != nil {
			return err
		}
	}
	return nil
}

func graphKind(k string) bool {
	switch k {
	case "mx_jvmci_library", "mx_shaded_java", "mx_jar_dist", "mx_resource_layout", "mx_classpath_dist", "mx_files", "mx_project", "mx_whole_java", "mx_processed_java", "mx_processor", "mx_test_class":
		return true
	}
	return false
}

func (g *importer) Kinds() map[string]rule.KindInfo {
	kinds := g.javaLanguage.Kinds()
	managed := map[string]bool{}
	for _, a := range strings.Fields("overlays shade shade_inputs manifest contents directives release snippets_pattern archived_libraries jni_services truffle_services graal_services module_name add_exports abi_after_javac copies literals srcs deps packages test_class project processor_class generates_api plugins resources mx_resource_root unified_release javac_opts javacopts testonly visibility") {
		managed[a] = true
	}
	for _, k := range strings.Fields("mx_jvmci_library mx_shaded_java mx_files mx_project mx_whole_java mx_processed_java mx_processor mx_test_class") {
		kinds[k] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"srcs": true, "packages": true, "project": true, "processor_class": true}, MergeableAttrs: managed}
	}
	kinds["mx_jar_dist"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"contents": true}, MergeableAttrs: managed}
	kinds["mx_resource_layout"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"literals": true, "copies": true}, MergeableAttrs: managed}
	kinds["mx_classpath_dist"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"deps": true}, MergeableAttrs: managed}
	if g.filename == "" {
		return kinds
	}
	k := kinds["java_library"]
	k.MergeableAttrs = maps.Clone(k.MergeableAttrs)
	for _, a := range []string{"javac_opts", "javacopts", "unified_release"} {
		k.MergeableAttrs[a] = true
	}
	kinds["java_library"] = k
	return kinds
}
func (g *importer) Loads() []rule.LoadInfo {
	return append(g.javaLanguage.Loads(), rule.LoadInfo{Name: "@rules_mx//mx:graph.bzl", Symbols: strings.Fields("mx_jvmci_library mx_shaded_java mx_jar_dist mx_classpath_dist mx_resource_layout mx_files mx_processed_java mx_processor mx_project mx_test_class mx_whole_java")})
}

func (g *importer) GenerateRules(a language.GenerateArgs) language.GenerateResult {
	if g.owners == nil {
		return g.javaLanguage.GenerateRules(a)
	}
	var res language.GenerateResult
	p := g.owners[a.Rel]
	if p != nil && len(p.Sources) == 0 {
		res = g.javaLanguage.GenerateRules(a)
		for _, r := range res.Gen {
			if r.Kind() != "java_library" {
				continue
			}
			if mapped := a.Config.KindMap["java_library"]; mapped.KindName == "elide_java_library" {
				r.SetAttr("javac_opts", p.Options)
				r.SetAttr("unified_release", p.Release)
			} else {
				r.SetAttr("javacopts", p.Options)
			}
		}
	}
	if a.Rel == g.suite || strings.HasPrefix(a.Rel+"/", g.suite+"/") || g.suite == "" {
		current := map[string]string{}
		for _, r := range g.rules[a.Rel] {
			if r.Kind() == "mx_project" {
				var packages []string
				configs := a.Config.Exts[g.Name()].(javaconfig.Configs)
				for _, dir := range g.projects[r.Name()].Dirs {
					pkg := path.Join(g.suite, dir)
					name := path.Base(pkg)
					if cfg := configs[pkg]; cfg != nil {
						name = cfg.MapLibraryName(name)
					}
					packages = append(packages, label.New("", pkg, name).String())
				}
				r.SetAttr("packages", packages)
			}
			res.Gen = append(res.Gen, r)
			res.Imports = append(res.Imports, nil)
			current[r.Name()] = r.Kind()
		}
		if a.File != nil {
			for _, r := range a.File.Rules {
				if !graphKind(r.Kind()) {
					continue
				}
				if a.Rel != g.suite && (r.Kind() != "mx_files" || !slices.Contains(r.AttrStrings("visibility"), "//"+g.suite+":__pkg__")) {
					continue
				}
				if kind := current[r.Name()]; kind != "" {
					if r.Kind() != kind {
						r.SetKind(kind)
					}
				} else {
					res.Empty = append(res.Empty, rule.NewRule(r.Kind(), r.Name()))
				}
			}
		}
	}
	return res
}

func (g *importer) Imports(c *config.Config, r *rule.Rule, f *rule.File) []resolve.ImportSpec {
	if !graphKind(r.Kind()) {
		return g.javaLanguage.Imports(c, r, f)
	}
	p := g.projects[r.Name()]
	if p == nil || len(p.Sources) == 0 || f.Pkg != g.suite {
		return nil
	}
	var specs []resolve.ImportSpec
	for _, pkg := range p.Packages {
		imp := pkg
		if p.Testonly {
			imp += "!testonly"
		}
		specs = append(specs, resolve.ImportSpec{Lang: g.Name(), Imp: imp})
	}
	return specs
}
func (g *importer) Embeds(r *rule.Rule, from label.Label) []label.Label {
	if graphKind(r.Kind()) {
		return nil
	}
	return g.javaLanguage.Embeds(r, from)
}
func (g *importer) Resolve(c *config.Config, ix *resolve.RuleIndex, rc *repo.RemoteCache, r *rule.Rule, imports interface{}, from label.Label) {
	if graphKind(r.Kind()) {
		return
	}
	g.javaLanguage.Resolve(c, ix, rc, r, imports, from)
	p := g.owners[from.Pkg]
	if p == nil || len(p.Sources) != 0 || r.Kind() != "java_library" {
		return
	}
	deps := map[string]bool{}
	for _, d := range r.AttrStrings("deps") {
		deps[d] = true
	}
	for _, d := range p.Deps {
		if strings.HasPrefix(d, ":") {
			// Gazelle already resolves imports from source-indexed projects to
			// their owning packages. The aggregate preserves the full mx edge.
			if project := g.projects[strings.TrimPrefix(d, ":")]; project != nil && len(project.Sources) == 0 {
				continue
			}
			d = "//" + g.suite + d
		}
		deps[d] = true
	}
	if len(deps) > 0 {
		all := make([]string, 0, len(deps))
		for d := range deps {
			all = append(all, d)
		}
		sort.Strings(all)
		r.SetAttr("deps", all)
	}
}
