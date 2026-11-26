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
	suite                                string
	Resolve                              map[string]string
	Name                                 string
	Release                              int
	Options, Dirs, Packages, Tests, Deps []string
	Sources, Resources, Processors       []string
	PatchModule                          string `json:"patch_module"`
	PatchRuntime                         string `json:"patch_runtime"`
	Roots                                []string
	JVMCI                                bool
	ForceJavac                           bool `json:"force_javac"`
	JNIHeaders                           bool `json:"jni_headers"`
	ABIAfterJavac                        bool `json:"abi_after_javac"`
	Shade                                string
	ShadeInputs                          []string `json:"shade_inputs"`
	AddExports                           []string `json:"add_exports"`
	ResourceRoot                         string   `json:"resource_root"`
	ResourceRoots                        []string `json:"resource_roots"`
	TestLauncher                         string   `json:"test_launcher"`
	ManualTests                          bool     `json:"manual_tests"`
	ManualTestClasses                    []string `json:"manual_test_classes"`
	TestRuntime                          []string `json:"test_runtime"`
	TestData                             []string `json:"test_data"`
	TestJVMFlags                         []string `json:"test_jvm_flags"`
	Testonly, Empty                      bool
}

type javaLanguage interface {
	language.Language
	language.LifecycleManager
}

type importer struct {
	javaLanguage
	filenames []string
	suites    map[string]bool
	owners    map[string]*project
	projects  map[label.Label]*project
	rules     map[string][]*rule.Rule
}

func NewLanguage() language.Language {
	return &importer{javaLanguage: java.NewLanguage().(javaLanguage)}
}

func (g *importer) RegisterFlags(fs *flag.FlagSet, cmd string, c *config.Config) {
	g.javaLanguage.RegisterFlags(fs, cmd, c)
	fs.Func("mx_graph", "Repository-relative mx bazel-buildfile --gazelle JSON (repeat for each suite)", func(filename string) error {
		g.filenames = append(g.filenames, filename)
		return nil
	})
}

func (g *importer) CheckFlags(fs *flag.FlagSet, c *config.Config) error {
	if err := g.javaLanguage.CheckFlags(fs, c); err != nil || len(g.filenames) == 0 {
		return err
	}
	g.suites = map[string]bool{}
	g.owners = map[string]*project{}
	g.projects = map[label.Label]*project{}
	g.rules = map[string][]*rule.Rule{}
	seen := map[label.Label]bool{}
	for _, filename := range g.filenames {
		if err := g.loadGraph(c.RepoRoot, filename, seen); err != nil {
			return err
		}
	}
	return nil
}

func (g *importer) loadGraph(root, filename string, seen map[label.Label]bool) error {
	if !filepath.IsLocal(filename) {
		return fmt.Errorf("-mx_graph must be repository-relative")
	}
	data, err := os.ReadFile(filepath.Join(root, filename))
	if err != nil {
		return err
	}
	var graph struct {
		Version       int
		Projects      []*project
		Distributions []struct {
			Manifest, Overlays         map[string]string
			Name, Module, Pom          string
			PomFilename                string   `json:"pom_filename"`
			RuntimeDeps                []string `json:"runtime_deps"`
			Deps, Contents, Directives []string
			AddExports                 []string `json:"add_exports"`
			ModuleExports              []string `json:"module_exports"`
			SnippetsPattern            string   `json:"snippets_pattern"`
			ArchivedLibraries          []string `json:"archived_libraries"`
			GraalTestServices          bool     `json:"graal_test_services"`
			GraalServices              bool     `json:"graal_services"`
			TruffleServices            bool     `json:"truffle_services"`
			JMHMetadata                bool     `json:"jmh_metadata"`
			JNIServices                bool     `json:"jni_services"`
			Release                    int
			Testonly                   bool
		}
		Layouts []struct {
			Name             string
			Literals, Copies map[string]string
			Testonly         bool
		}
		NativeShared []struct {
			Name, Root          string
			Srcs, Headers, Deps []string
			JDKStaticLibraries  []string `json:"jdk_static_libraries"`
			Profiles            map[string]map[string]interface{}
			Testonly            bool
		} `json:"native_shared"`
		SourceProjects []struct {
			Name     string
			Members  map[string]string
			Srcs     []string
			Testonly bool
		} `json:"source_projects"`
		ToolLayouts []struct {
			Name, Tool, Launcher string
			Links                map[string]string
			WindowsCommand       string `json:"windows_command"`
		} `json:"tool_layouts"`
		BashLaunchers []struct {
			Name, Output          string
			Classpath, Modulepath []string
			JDKArchive            string   `json:"jdk_archive"`
			JavaArgs              []string `json:"java_args"`
		} `json:"bash_launchers"`
		ThinLaunchers []struct {
			Name          string
			Srcs          []string
			PlatformCopts map[string][]string `json:"platform_copts"`
		} `json:"thin_launchers"`
		ImageProjects []struct {
			Shared                       bool
			Name, Main, Output           string
			Classpath, Modulepath, Flags []string
		} `json:"image_projects"`
		WasmPrograms []struct {
			Name, Root, Wat2wasm, Wasm2wat, Includes string
			Cases, Native, Sidecars                  []string
			Copts, Exports                           map[string][]string
			Testonly                                 bool
		} `json:"wasm_programs"`
		WatProjects []struct {
			Name, Root, Tool string
			Cases            []string
			Testonly         bool
		} `json:"wat_projects"`
		CMakeSuites []struct {
			Name, Root, SDK, Modules   string
			Cases, Languages, Variants []string
			Config                     map[string]string
			Testonly                   bool
		} `json:"cmake_suites"`
		BitcodeSuites []struct {
			Name, Root, Linker string
			Cases              []string
			Testonly           bool
		} `json:"bitcode_suites"`
		DistributionAliases map[string]string `json:"distribution_aliases"`
		ReleaseArchives     []struct {
			Name, Install, Prefix string
		} `json:"release_archives"`
		SourceExports map[string]map[string]string `json:"source_exports"`
		TarLayouts    []struct {
			OSLayouts      map[string]map[string]interface{} `json:"os_layouts"`
			JDKHomes       map[string]string                 `json:"jdk_homes"`
			Format         string
			Name           string
			Members        map[string]string
			ArchiveImports map[string]string            `json:"archive_imports"`
			FileList       []string                     `json:"file_list"`
			SourceArchives map[string]map[string]string `json:"source_archives"`
			Macro          *struct {
				Name            string
				Lines, Builders []string
				JavaArgs        []string `json:"java_args"`
			}
			Testonly bool
		} `json:"tar_layouts"`
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
	suite := path.Dir(filepath.ToSlash(filename))
	if suite == "." {
		suite = ""
	}
	if g.suites[suite] {
		return fmt.Errorf("%s: more than one mx graph provided for package %q", filename, suite)
	}
	g.suites[suite] = true
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
			pkg := path.Dir(path.Join(suite, file))
			if pkg == "." {
				pkg = ""
			}
			grouped[pkg] = append(grouped[pkg], path.Base(file))
		}
		var labels []string
		for pkg, members := range grouped {
			r := rule.NewRule("mx_files", p.Name+"."+attr)
			r.SetAttr("srcs", members)
			r.SetAttr("visibility", []string{"//" + suite + ":__pkg__"})
			if err := add(pkg, r); err != nil {
				return nil, err
			}
			labels = append(labels, label.New("", pkg, r.Name()).String())
		}
		sort.Strings(labels)
		return labels, nil
	}
	for _, p := range graph.Projects {
		if p == nil || p.Name == "" || (len(p.Dirs) == 0 && !p.Empty) || p.Release < 1 {
			return fmt.Errorf("%s: expected a Java project with a name, source directories and release", filename)
		}
		p.suite = suite
		g.projects[label.New("", suite, p.Name)] = p
		for _, dir := range p.Dirs {
			if !filepath.IsLocal(dir) {
				return fmt.Errorf("invalid directory %q for %s", dir, p.Name)
			}
			pkg := path.Join(suite, dir)
			if prev := g.owners[pkg]; prev != nil {
				return fmt.Errorf("%s and %s both own %s", prev.Name, p.Name, pkg)
			}
			g.owners[pkg] = p
		}
		var r *rule.Rule
		if len(p.Sources) != 0 || p.Shade != "" {
			kind, opts := "mx_whole_java", "javac_opts"
			if p.PatchModule != "" {
				kind, opts = "mx_patch_module", "javacopts"
			} else if p.Shade != "" {
				kind, opts = "mx_shaded_java", "javacopts"
			} else if p.JVMCI || p.ForceJavac {
				kind, opts = "mx_jvmci_library", "javacopts"
			} else if p.JNIHeaders || len(p.Processors) != 0 || len(p.AddExports) != 0 {
				kind, opts = "mx_processed_java", "javacopts"
			}
			r = rule.NewRule(kind, p.Name)
			if p.PatchModule != "" {
				r.SetAttr("module_name", p.PatchModule)
				r.SetAttr("roots", p.Roots)
				r.SetAttr("runtime", p.PatchRuntime)
			}
			r.SetAttr(opts, p.Options)
			if p.JNIHeaders {
				r.SetAttr("jni_headers", true)
			}
			if p.Shade != "" {
				r.SetAttr("shade", p.Shade)
				r.SetAttr("shade_inputs", p.ShadeInputs)
				r.SetAttr("release", p.Release)
			} else if len(p.AddExports) != 0 {
				r.SetAttr("add_exports", p.AddExports)
			}
			if len(p.Processors) != 0 {
				r.SetAttr("plugins", p.Processors)
				if p.ABIAfterJavac && kind != "mx_jvmci_library" {
					r.SetAttr("abi_after_javac", true)
				}
			} else if kind == "mx_whole_java" {
				r.SetAttr("unified_release", p.Release)
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
			if len(p.ResourceRoots) > 0 {
				r.SetAttr("mx_resource_roots", p.ResourceRoots)
			}
		} else {
			r = rule.NewRule("mx_project", p.Name)
		}
		if p.Testonly {
			r.SetAttr("testonly", true)
		}
		if len(p.Deps) > 0 {
			r.SetAttr("deps", p.Deps)
		}
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
		for _, class := range p.Tests {
			t := rule.NewRule("mx_test_class", p.Name+"."+class)
			if p.TestLauncher != "" {
				t.SetAttr("launcher", p.TestLauncher)
			} else if len(p.TestRuntime) > 0 {
				t.SetAttr("runtime_deps", p.TestRuntime)
			} else {
				t.SetAttr("project", ":"+p.Name)
			}
			if len(p.TestData) > 0 {
				t.SetAttr("data", p.TestData)
			}
			if len(p.TestJVMFlags) > 0 {
				t.SetAttr("jvm_flags", p.TestJVMFlags)
			}
			if p.ManualTests || slices.Contains(p.ManualTestClasses, class) {
				t.SetAttr("tags", []string{"manual"})
			}
			t.SetAttr("test_class", class)
			if err := add(suite, t); err != nil {
				return err
			}
		}
	}
	for _, dist := range graph.Distributions {
		r := rule.NewRule("mx_classpath_dist", dist.Name)
		if dist.Pom != "" {
			r.SetKind("mx_pom_dist")
			r.SetAttr("content", dist.Pom)
			r.SetAttr("out", dist.PomFilename)
			if len(dist.RuntimeDeps) > 0 {
				r.SetAttr("runtime_deps", dist.RuntimeDeps)
			}
		} else if len(dist.Contents) > 0 {
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
				if dist.GraalTestServices {
					r.SetAttr("graal_test_services", true)
				}
			}
			if dist.TruffleServices {
				r.SetAttr("truffle_services", true)
			}
			if dist.JMHMetadata {
				r.SetAttr("jmh_metadata", true)
			}
			if dist.JNIServices {
				r.SetAttr("jni_services", true)
			}
			if len(dist.ModuleExports) > 0 {
				r.SetAttr("module_exports", dist.ModuleExports)
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
		if err := add(suite, r); err != nil {
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
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, p := range graph.NativeShared {
		r := rule.NewRule("mx_native_shared_project", p.Name)
		r.SetAttr("root", p.Root)
		r.SetAttr("profiles", p.Profiles)
		r.SetAttr("srcs", p.Srcs)
		r.SetAttr("headers", p.Headers)
		if len(p.JDKStaticLibraries) > 0 {
			r.SetAttr("jdk_static_libraries", p.JDKStaticLibraries)
		}
		if len(p.Deps) > 0 {
			r.SetAttr("deps", p.Deps)
		}
		r.SetAttr("testonly", p.Testonly)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, p := range graph.SourceProjects {
		r := rule.NewRule("mx_project_files", p.Name)
		if len(p.Members) > 0 {
			r.SetAttr("members", p.Members)
		}
		if len(p.Srcs) > 0 {
			r.SetAttr("srcs", p.Srcs)
		}
		r.SetAttr("testonly", p.Testonly)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, layout := range graph.ToolLayouts {
		r := rule.NewRule("mx_toolchain_tool_layout", layout.Name)
		r.SetAttr("tool", layout.Tool)
		if layout.Launcher != "" {
			r.SetAttr("launcher", layout.Launcher)
		}
		r.SetAttr("links", layout.Links)
		r.SetAttr("windows_command", layout.WindowsCommand)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, launcher := range graph.BashLaunchers {
		r := rule.NewRule("mx_jvm_tool_launcher", launcher.Name)
		r.SetAttr("output", launcher.Output)
		r.SetAttr("jdk_archive", launcher.JDKArchive)
		r.SetAttr("java_args", launcher.JavaArgs)
		if len(launcher.Classpath) > 0 {
			r.SetAttr("classpath", launcher.Classpath)
		}
		if len(launcher.Modulepath) > 0 {
			r.SetAttr("modulepath", launcher.Modulepath)
		}
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, launcher := range graph.ThinLaunchers {
		r := rule.NewRule("mx_thin_launcher", launcher.Name)
		r.SetAttr("srcs", launcher.Srcs)
		r.SetAttr("platform_copts", launcher.PlatformCopts)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, image := range graph.ImageProjects {
		r := rule.NewRule("mx_native_image_executable", image.Name)
		if image.Shared {
			r.SetKind("mx_native_image_library")
		} else {
			r.SetAttr("main_class", image.Main)
		}
		r.SetAttr("output", image.Output)
		r.SetAttr("image_args", image.Flags)
		if len(image.Classpath) != 0 {
			r.SetAttr("classpath", image.Classpath)
		}
		if len(image.Modulepath) != 0 {
			r.SetAttr("modulepath", image.Modulepath)
		}
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, p := range graph.WasmPrograms {
		r := rule.NewRule("mx_wasm_programs", p.Name)
		r.SetAttr("root", p.Root)
		r.SetAttr("cases", p.Cases)
		r.SetAttr("exports", p.Exports)
		if len(p.Native) > 0 {
			r.SetAttr("native_cases", p.Native)
		}
		if len(p.Copts) > 0 {
			r.SetAttr("copts", p.Copts)
		}
		if p.Includes != "" {
			r.SetAttr("includes", p.Includes)
		}
		r.SetAttr("sidecars", p.Sidecars)
		r.SetAttr("wat2wasm", p.Wat2wasm)
		r.SetAttr("wasm2wat", p.Wasm2wat)
		r.SetAttr("testonly", p.Testonly)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, p := range graph.WatProjects {
		r := rule.NewRule("mx_wat_project", p.Name)
		r.SetAttr("root", p.Root)
		r.SetAttr("cases", p.Cases)
		r.SetAttr("tool", p.Tool)
		r.SetAttr("testonly", p.Testonly)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, project := range graph.CMakeSuites {
		r := rule.NewRule("mx_sulong_cmake_suite", project.Name)
		r.SetAttr("root", project.Root)
		r.SetAttr("cases", project.Cases)
		r.SetAttr("sdk", project.SDK)
		r.SetAttr("modules", project.Modules)
		r.SetAttr("languages", project.Languages)
		r.SetAttr("variants", project.Variants)
		if len(project.Config) != 0 {
			r.SetAttr("config", project.Config)
		}
		r.SetAttr("testonly", project.Testonly)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, project := range graph.BitcodeSuites {
		r := rule.NewRule("mx_bitcode_suite", project.Name)
		r.SetAttr("root", project.Root)
		r.SetAttr("cases", project.Cases)
		r.SetAttr("llvm_link", project.Linker)
		r.SetAttr("testonly", project.Testonly)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, name := range slices.Sorted(maps.Keys(graph.DistributionAliases)) {
		actual := graph.DistributionAliases[name]
		r := rule.NewRule("mx_distribution_alias", name)
		r.SetAttr("actual", ":"+actual)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, archive := range graph.ReleaseArchives {
		r := rule.NewRule("mx_release_archive", archive.Name)
		r.SetAttr("install", archive.Install)
		r.SetAttr("prefix", archive.Prefix)
		r.SetAttr("visibility", []string{"//visibility:public"})
		if err := add(suite, r); err != nil {
			return err
		}
	}
	addSources := func(name string, files map[string]string, testonly, public bool) error {
		byDir := map[string][]string{}
		for src, dest := range files {
			if !filepath.IsLocal(src) || !filepath.IsLocal(dest) || path.Base(src) != path.Base(dest) {
				return fmt.Errorf("invalid source archive member %s -> %s", src, dest)
			}
			dir := path.Dir(dest)
			byDir[dir] = append(byDir[dir], src)
		}
		groups := map[string]string{}
		for dir, srcs := range byDir {
			sort.Strings(srcs)
			labels, err := groupFiles(&project{Name: name}, "files", srcs)
			if err != nil {
				return err
			}
			for _, label := range labels {
				groups[label] = dir
			}
		}
		archive := rule.NewRule("mx_source_archive", name)
		archive.SetAttr("groups", groups)
		archive.SetAttr("testonly", testonly)
		if public {
			archive.SetAttr("visibility", []string{"//visibility:public"})
		}
		if err := add(suite, archive); err != nil {
			return err
		}
		return nil
	}
	for _, name := range slices.Sorted(maps.Keys(graph.SourceExports)) {
		if err := addSources(name+".sources", graph.SourceExports[name], false, true); err != nil {
			return err
		}
	}
	for _, layout := range graph.TarLayouts {
		if macro := layout.Macro; macro != nil {
			r := rule.NewRule("mx_native_image_properties", macro.Name)
			r.SetAttr("lines", macro.Lines)
			r.SetAttr("builders", macro.Builders)
			r.SetAttr("java_args", macro.JavaArgs)
			if err := add(suite, r); err != nil {
				return err
			}
		}
		for _, name := range slices.Sorted(maps.Keys(layout.SourceArchives)) {
			if err := addSources(name, layout.SourceArchives[name], layout.Testonly, false); err != nil {
				return err
			}
		}
		r := rule.NewRule("mx_exported_tar", layout.Name)
		format := layout.Format
		if format == "" {
			format = "tar"
		}
		r.SetAttr("format", format)
		if len(layout.JDKHomes) > 0 {
			r.SetAttr("jdk_homes", layout.JDKHomes)
		}
		if len(layout.OSLayouts) != 0 {
			r.SetKind("mx_os_layout")
			r.SetAttr("os_layouts", layout.OSLayouts)
		} else {
			r.SetAttr("members", layout.Members)
		}
		if len(layout.FileList) > 0 {
			r.SetAttr("file_list", layout.FileList)
		}
		if len(layout.ArchiveImports) > 0 {
			r.SetAttr("archive_imports", layout.ArchiveImports)
		}
		r.SetAttr("visibility", []string{"//visibility:public"})
		if layout.Testonly {
			r.SetAttr("testonly", true)
		}
		if err := add(suite, r); err != nil {
			return err
		}
	}
	for _, plugin := range graph.Plugins {
		r := rule.NewRule("mx_processor", plugin.Name)
		r.SetAttr("processor_class", plugin.ProcessorClass)
		r.SetAttr("visibility", []string{"//visibility:public"})
		r.SetAttr("generates_api", plugin.GeneratesAPI)
		r.SetAttr("deps", plugin.Deps)
		if err := add(suite, r); err != nil {
			return err
		}
	}
	return nil
}

func graphKind(k string) bool {
	switch k {
	case "mx_native_shared_project", "mx_os_layout", "mx_jvm_tool_launcher", "mx_distribution_alias", "mx_release_archive", "mx_native_image_library", "mx_thin_launcher", "mx_toolchain_tool_layout", "mx_native_image_executable", "mx_patch_module", "mx_native_image_properties", "mx_source_archive", "mx_wasm_programs", "mx_project_files", "mx_wat_project", "mx_sulong_cmake_suite", "mx_bitcode_suite", "mx_exported_tar", "mx_jvmci_library", "mx_shaded_java", "mx_jar_dist", "mx_pom_dist", "mx_resource_layout", "mx_classpath_dist", "mx_files", "mx_project", "mx_whole_java", "mx_processed_java", "mx_processor", "mx_test_class":
		return true
	}
	return false
}

func (g *importer) Kinds() map[string]rule.KindInfo {
	kinds := g.javaLanguage.Kinds()
	managed := map[string]bool{}
	for _, a := range strings.Fields("jdk_static_libraries headers profiles os_layouts jdk_archive launcher actual install prefix jdk_homes platform_copts main_class output image_args classpath modulepath links windows_command runtime roots builders lines java_args groups exports native_cases copts includes sidecars wat2wasm wasm2wat tool sdk modules languages variants config root cases llvm_link format members file_list archive_imports overlays shade shade_inputs manifest out content contents directives release snippets_pattern archived_libraries jmh_metadata jni_services truffle_services graal_services graal_test_services module_name add_exports module_exports jni_headers abi_after_javac copies literals srcs deps packages launcher tags data runtime_deps jvm_flags test_class project processor_class generates_api plugins resources mx_resource_root mx_resource_roots unified_release javac_opts javacopts testonly visibility") {
		managed[a] = true
	}
	for _, k := range strings.Fields("mx_thin_launcher mx_patch_module mx_jvmci_library mx_shaded_java mx_files mx_project mx_whole_java mx_processed_java mx_processor mx_test_class") {
		kinds[k] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"srcs": true, "packages": true, "project": true, "test_class": true, "processor_class": true}, MergeableAttrs: managed}
	}
	kinds["mx_native_image_properties"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"lines": true}, MergeableAttrs: managed}
	kinds["mx_source_archive"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"groups": true}, MergeableAttrs: managed}
	kinds["mx_project_files"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"members": true, "srcs": true}, MergeableAttrs: managed}
	kinds["mx_native_shared_project"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"profiles": true}, MergeableAttrs: managed}
	kinds["mx_os_layout"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"os_layouts": true}, MergeableAttrs: managed}
	kinds["mx_jvm_tool_launcher"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"jdk_archive": true}, MergeableAttrs: managed}
	kinds["mx_toolchain_tool_layout"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"tool": true}, MergeableAttrs: managed}
	kinds["mx_native_image_library"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"output": true}, MergeableAttrs: managed}
	kinds["mx_native_image_executable"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"main_class": true}, MergeableAttrs: managed}
	kinds["mx_wasm_programs"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"cases": true}, MergeableAttrs: managed}
	kinds["mx_wat_project"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"cases": true}, MergeableAttrs: managed}
	kinds["mx_sulong_cmake_suite"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"cases": true}, MergeableAttrs: managed}
	kinds["mx_bitcode_suite"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"cases": true}, MergeableAttrs: managed}
	kinds["mx_distribution_alias"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"actual": true}, MergeableAttrs: managed}
	kinds["mx_release_archive"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"install": true}, MergeableAttrs: managed}
	kinds["mx_exported_tar"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"members": true, "archive_imports": true}, MergeableAttrs: managed}
	kinds["mx_jar_dist"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"contents": true}, MergeableAttrs: managed}
	kinds["mx_pom_dist"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"content": true}, MergeableAttrs: managed}
	kinds["mx_resource_layout"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"literals": true, "copies": true}, MergeableAttrs: managed}
	kinds["mx_classpath_dist"] = rule.KindInfo{NonEmptyAttrs: map[string]bool{"deps": true}, MergeableAttrs: managed}
	if len(g.filenames) == 0 {
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
	return append(g.javaLanguage.Loads(),
		rule.LoadInfo{Name: "@rules_mx//mx:native_project.bzl", Symbols: []string{"mx_native_shared_project"}},
		rule.LoadInfo{Name: "@rules_mx//graal:layout.bzl", Symbols: []string{"mx_os_layout"}},
		rule.LoadInfo{Name: "@rules_mx//mx:thin_launcher.bzl", Symbols: []string{"mx_thin_launcher"}},
		rule.LoadInfo{Name: "@rules_mx//mx:native_image.bzl", Symbols: []string{"mx_jvm_tool_launcher", "mx_native_image_executable", "mx_native_image_library", "mx_toolchain_tool_layout"}},
		rule.LoadInfo{Name: "@rules_mx//mx:wasm_programs.bzl", Symbols: []string{"mx_wasm_programs"}},
		rule.LoadInfo{Name: "@rules_mx//mx:jvmci.bzl", Symbols: []string{"mx_patch_module"}},
		rule.LoadInfo{Name: "@rules_mx//mx:jar.bzl", Symbols: []string{"mx_native_image_properties"}},
		rule.LoadInfo{Name: "@rules_mx//mx:project_files.bzl", Symbols: []string{"mx_project_files", "mx_source_archive", "mx_wat_project"}},
		rule.LoadInfo{Name: "@rules_mx//mx:sulong_cmake.bzl", Symbols: []string{"mx_sulong_cmake_suite"}},
		rule.LoadInfo{Name: "@rules_mx//mx:bitcode.bzl", Symbols: []string{"mx_bitcode_suite"}},
		rule.LoadInfo{Name: "@rules_mx//mx:graph.bzl", Symbols: strings.Fields("mx_release_archive mx_distribution_alias mx_exported_tar mx_jvmci_library mx_shaded_java mx_jar_dist mx_classpath_dist mx_pom_dist mx_resource_layout mx_files mx_processed_java mx_processor mx_project mx_test_class mx_whole_java")},
	)
}

// A nested suite owns regeneration within its own packages.
func (g *importer) suiteFor(pkg string) (string, bool) {
	for {
		if g.suites[pkg] {
			return pkg, true
		}
		if pkg == "" {
			return "", false
		}
		parent := path.Dir(pkg)
		if parent == "." {
			parent = ""
		}
		pkg = parent
	}
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
	if suite, ok := g.suiteFor(a.Rel); ok {
		current := map[string]string{}
		for _, r := range g.rules[a.Rel] {
			if r.Kind() == "mx_project" {
				var packages []string
				configs := a.Config.Exts[g.Name()].(javaconfig.Configs)
				for _, dir := range g.projects[label.New("", a.Rel, r.Name())].Dirs {
					pkg := path.Join(suite, dir)
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
				if a.Rel != suite && (r.Kind() != "mx_files" || !slices.Contains(r.AttrStrings("visibility"), "//"+suite+":__pkg__")) {
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
	p := g.projects[label.New("", f.Pkg, r.Name())]
	if p == nil || len(p.Sources) == 0 {
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
	p := g.owners[from.Pkg]
	if p != nil && len(p.Resolve) > 0 {
		// Use only bindings on this project's mx classpath. BUILD directives take precedence.
		var file rule.File
		for pkg, target := range p.Resolve {
			imp := resolve.ImportSpec{Lang: g.Name(), Imp: pkg}
			if _, found := resolve.FindRuleWithOverride(c, imp, g.Name()); !found {
				file.Directives = append(file.Directives, rule.Directive{Key: "resolve", Value: g.Name() + " " + pkg + " " + target})
			}
		}
		c = c.Clone()
		new(resolve.Configurer).Configure(c, from.Pkg, &file)
	}
	g.javaLanguage.Resolve(c, ix, rc, r, imports, from)
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
			if project := g.projects[label.New("", p.suite, strings.TrimPrefix(d, ":"))]; project != nil && len(project.Sources) == 0 {
				continue
			}
			d = "//" + p.suite + d
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
