package proc;

import java.io.IOException;
import java.io.Writer;
import java.util.Set;
import javax.annotation.processing.AbstractProcessor;
import javax.annotation.processing.RoundEnvironment;
import javax.annotation.processing.SupportedAnnotationTypes;
import javax.annotation.processing.SupportedSourceVersion;
import javax.lang.model.SourceVersion;
import javax.lang.model.element.TypeElement;
import javax.tools.StandardLocation;

@SupportedAnnotationTypes("*")
@SupportedSourceVersion(SourceVersion.RELEASE_17)
public final class ManifestProcessor extends AbstractProcessor {
    @Override
    public boolean process(Set<? extends TypeElement> annotations, RoundEnvironment round) {
        if (round.processingOver()) {
            try (Writer out = processingEnv.getFiler()
                    .createResource(StandardLocation.CLASS_OUTPUT, "", "META-INF/processed.txt")
                    .openWriter()) {
                out.write("processed\n");
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        }
        return false;
    }
}
