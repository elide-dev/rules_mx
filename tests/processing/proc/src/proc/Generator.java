package proc;

import java.io.IOException;
import java.io.Writer;
import java.util.Set;
import javax.annotation.processing.*;
import javax.lang.model.SourceVersion;
import javax.lang.model.element.TypeElement;

@SupportedAnnotationTypes("*")
@SupportedSourceVersion(SourceVersion.RELEASE_17)
public final class Generator extends AbstractProcessor {
    private boolean generated;

    @Override
    public boolean process(Set<? extends TypeElement> annotations, RoundEnvironment round) {
        if (!generated && !round.processingOver()) {
            generated = true;
            try (Writer out = processingEnv.getFiler().createSourceFile("app.Generated").openWriter()) {
                out.write("package app; public final class Generated {}");
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        }
        return false;
    }
}
