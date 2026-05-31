import random
import sys

def object_declaration(num, name):
    out = ""
    for x in range(num-1):
        out = out +name+"_"+str(x)+" "
    out = out +name+"_"+str(num-1) +" - "+name
    return out
    


def main(argv):
    if(len(argv) != 5):
        print("INPUT ERROR")
        print("args: num_nurse, num_doctor, num_toolset, num_patient, problem_num")
        sys.exit()
    
    #====================================
    #VARIABLES
    num_nurse = int(argv[0])
    num_doctor = int(argv[1])
    num_toolset = int(argv[2])
    num_patient = int(argv[3])
    problem_num = int(argv[4])


    #=====================================
    filename="pfile"+str(problem_num)

    f = open(filename+".hddl", "w")

    f.write("; num_nurse: "+str(num_nurse)+"\n")
    f.write("; num_doctor: "+str(num_doctor)+"\n")
    f.write("; num_patient: "+str(num_patient)+"\n")
    f.write("; num_toolset: "+str(num_toolset)+"\n\n")
    
    f.write("(define\n")
    f.write("   (problem "+filename+")\n")
    f.write("   (:domain medical)\n")
    f.write("   (:objects\n")

    obj = object_declaration(num_nurse, "nurse")
    f.write("      "+obj+"\n")
    obj = object_declaration(num_doctor, "doctor")
    f.write("      "+obj+"\n")
    obj = object_declaration(num_patient, "patient")
    f.write("      "+obj+"\n")
    obj = object_declaration(num_toolset, "toolset")
    f.write("      "+obj+"\n")



    f.write("   )\n\n")
    f.write("   (:htn\n")
    f.write("      :parameters ()\n")
    f.write("      :subtasks (and\n")

    tasks = ""
    for x in range(num_patient):
        f.write("         (task"+str(x)+" (operate patient_"+str(x)+"))\n")
    f.write("      )\n")
    f.write("   )\n\n")
    f.write("   (:init\n")

    
    f.write("   )\n")
    f.write(")")



    f.close()

    #open and read the file after the appending:
    f = open(filename+".hddl", "r")
    print(f.read())

if __name__ == "__main__":
   main(sys.argv[1:])
